from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.db.models import Q
from .models import Atendimento, PerfilUsuario, Cliente, Veiculo
from .forms import AtendimentoForm, ClienteForm, VeiculoForm  # Certifique-se de criar esses forms

from django.db import transaction
from django.http import JsonResponse
import re
from .models import Atendimento, PerfilUsuario, Cliente, Veiculo, TipoServico
from django.db.models import Count
import datetime
from django.contrib import messages # Importante para avisar o erro na tela
from .models import TipoServico, Cliente, Atendimento
from .models import Orcamento, ItemOrcamento
import json
from django.db.models.functions import ExtractMonth

from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login
from django.contrib.sessions.models import Session
from .models import PerfilUsuario  # Importe seu modelo de perfil criado
from .asaas import gerar_boleto_asaas
from django.contrib.auth.models import User
from .forms import UsuarioMasterEditForm 
from django.core.cache import cache

from django.http import FileResponse
from .forms import CompressaoPDFForm
from .utils import comprimir_pdf_memoria
from django.urls import reverse
import base64

from django.contrib.auth.decorators import user_passes_test
from django.db.models import Sum
from .models import Despachante, PerfilUsuario
from .asaas import gerar_boleto_asaas
from datetime import timedelta
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.hashers import make_password
from django.db import transaction
from .forms import AtendimentoForm, ClienteForm, VeiculoForm, CompressaoPDFForm, DespachanteForm, UsuarioMasterForm

# ==============================================================================
# 1. VIEW DE LOGIN (Com Bloqueio de Validade + E-mail + Single Session)
# ==============================================================================
def minha_view_de_login(request):
    contexto = {'erro_login': False}

    if request.method == 'POST':
        # 1. Pega dados do form
        login_input = request.POST.get('username') 
        password_form = request.POST.get('password')
        
        username_para_autenticar = login_input

        # --- LÓGICA: Verificar se é E-mail ---
        if '@' in login_input:
            try:
                user_obj = User.objects.get(email=login_input)
                username_para_autenticar = user_obj.username
            except User.DoesNotExist:
                # Se não achar, segue com o texto original (o authenticate vai falhar)
                pass
        # ------------------------------------------

        # 2. Autentica
        user = authenticate(request, username=username_para_autenticar, password=password_form)

        if user is not None:
            # =================================================================
            # ⛔ BLOQUEIO FINANCEIRO (NOVO)
            # Verifica se a assinatura venceu antes de deixar entrar
            # =================================================================
            try:
                # Tenta pegar o perfil (Superusuários podem não ter perfil, por isso o try)
                perfil_check = user.perfilusuario
                
                # Se tiver data definida E a data for menor que hoje (passado)
                if perfil_check.data_expiracao and perfil_check.data_expiracao < timezone.now().date():
                    
                    # Formata a data para mostrar na mensagem (Ex: 05/01/2026)
                    data_venc = perfil_check.data_expiracao.strftime('%d/%m/%Y')
                    
                    # Mensagem de erro para o usuário
                    messages.error(request, f"🔒 Acesso Bloqueado: Sua assinatura venceu em {data_venc}. Entre em contato com o suporte para regularizar.")
                    
                    # Impede o login e devolve para a tela
                    contexto['erro_login'] = True
                    return render(request, 'login.html', context=contexto)
            
            except AttributeError:
                # Se for um superuser sem perfil cadastrado, deixa passar (ou trate como preferir)
                pass
            # =================================================================

            # 3. Se passou pelo bloqueio, faz o login oficial
            login(request, user)

            # GARANTIA: Se a sessão não tiver chave ainda, força criar
            if not request.session.session_key:
                request.session.create()

            nova_chave = request.session.session_key

            # 4. Lógica de Single Session (Um dispositivo por vez)
            perfil, created = PerfilUsuario.objects.get_or_create(user=user)
            chave_antiga = perfil.ultimo_session_key

            if chave_antiga and chave_antiga != nova_chave:
                try:
                    # Tenta apagar a sessão anterior do banco (derruba o outro PC)
                    Session.objects.get(session_key=chave_antiga).delete()
                except Session.DoesNotExist:
                    pass

            # Atualiza o perfil com a chave atual
            perfil.ultimo_session_key = nova_chave
            perfil.save()

            return redirect('dashboard')
        
        else:
            # Senha incorreta ou usuário não encontrado
            contexto['erro_login'] = True
            # Adiciona mensagem visual se seu template suportar
            messages.error(request, "Usuário ou senha incorretos.")

    return render(request, 'login.html', context=contexto)

# ==============================================================================
# 2. VIEW DE PAGAMENTO (Nova função)
# ==============================================================================
@login_required
def pagar_mensalidade(request):
    """
    Função chamada pelo botão 'Pagar Agora' no Dashboard.
    Gera o boleto no Asaas e redireciona o usuário para a tela de pagamento.
    """
    # 1. Tenta pegar o despachante do usuário logado
    try:
        # Verifica se o usuário tem perfil e despachante vinculado
        despachante = request.user.perfilusuario.despachante
    except AttributeError:
        messages.error(request, "Usuário sem perfil de despachante vinculado.")
        return redirect('dashboard')

    # 2. Chama a função do arquivo asaas.py
    resultado = gerar_boleto_asaas(despachante)

    # 3. Verifica se deu certo
    if resultado['sucesso']:
        # Se deu certo, manda o usuário direto para o link da fatura (Pix/Boleto)
        return redirect(resultado['link_fatura'])
    else:
        # Se deu erro, mostra aviso na tela e volta pro dashboard
        messages.error(request, f"Erro ao gerar fatura: {resultado.get('erro')}")
        return redirect('dashboard')
# ==============================================================================
# DASHBOARD E FLUXO PRINCIPAL
# ==============================================================================

@login_required
def dashboard(request):
    try:
        perfil = request.user.perfilusuario
    except PerfilUsuario.DoesNotExist:
        return render(request, 'erro_perfil.html') 
    
    despachante = perfil.despachante
    
    # --- 1. CAPTURA OS FILTROS ---
    data_filtro = request.GET.get('data_filtro')
    termo_busca = request.GET.get('busca')

    # ==============================================================================
    # 🚀 OTIMIZAÇÃO 1: CACHE DE ESTATÍSTICAS (Novo)
    # ==============================================================================
    # Cria uma chave única para este escritório
    cache_key = f"dashboard_stats_{despachante.id}"
    
    # Tenta pegar os dados prontos da memória RAM
    dados_stats = cache.get(cache_key)

    if not dados_stats:
        # SE NÃO TIVER NO CACHE, CALCULA NO BANCO (Isso só roda 1x a cada 5 min)
        # ou quando o Signal limpar o cache.
        hoje = timezone.now().date()
        
        total_abertos = Atendimento.objects.filter(
            despachante=despachante
        ).exclude(status__in=['APROVADO', 'CANCELADO']).count()
        
        total_mes = Atendimento.objects.filter(
            despachante=despachante, 
            data_solicitacao__month=hoje.month
        ).count()
        
        # Guarda no dicionário
        dados_stats = {
            'total_abertos': total_abertos,
            'total_mes': total_mes
        }
        
        # Salva na memória por 300 segundos (5 minutos)
        cache.set(cache_key, dados_stats, timeout=300)

    # ==============================================================================
    # 🚀 OTIMIZAÇÃO 2: select_related (Mantido)
    # ==============================================================================
    fila_processos = Atendimento.objects.select_related(
        'cliente', 
        'veiculo',
        'responsavel' 
    ).filter(
        despachante=despachante
    ).exclude(
        status__in=['APROVADO', 'CANCELADO']
    ).order_by('data_solicitacao')
    
    # --- 2. FILTROS ---
    if data_filtro:
        fila_processos = fila_processos.filter(data_solicitacao=data_filtro)

    if termo_busca:
        fila_processos = fila_processos.filter(
            Q(cliente__nome__icontains=termo_busca) |
            Q(veiculo__placa__icontains=termo_busca) |
            Q(numero_atendimento__icontains=termo_busca) |
            Q(servico__icontains=termo_busca)
        )
    
    # --- 3. LÓGICA DE ALERTAS (Processamento Python) ---
    hoje = timezone.now().date()
    for processo in fila_processos:
        if processo.data_entrega:
            dias_restantes = (processo.data_entrega - hoje).days
            processo.dias_na_fila = dias_restantes
            if dias_restantes < 0:
                processo.alerta_cor = 'danger'; processo.alerta_msg = 'Atrasado'
            elif dias_restantes <= 2:
                processo.alerta_cor = 'warning'; processo.alerta_msg = 'Vence Logo'
            else:
                processo.alerta_cor = 'success'; processo.alerta_msg = 'No Prazo'
        else:
            dias_corridos = (hoje - processo.data_solicitacao).days
            processo.dias_na_fila = dias_corridos
            if dias_corridos >= 30:
                processo.alerta_cor = 'danger'; processo.alerta_msg = 'Crítico'
            elif dias_corridos >= 15:
                processo.alerta_cor = 'warning'; processo.alerta_msg = 'Atenção'
            else:
                processo.alerta_cor = 'success'; processo.alerta_msg = 'Recente'

    context = {
        'fila_processos': fila_processos,
        # AQUI MUDOU: Pegamos os valores do dicionário cached
        'total_abertos': dados_stats['total_abertos'], 
        'total_mes': dados_stats['total_mes'],
        'perfil': perfil,
        'data_filtro': data_filtro,
        'termo_busca': termo_busca,
    }
    
    return render(request, 'dashboard.html', context)

# ==============================================================================
# GESTÃO DE ATENDIMENTOS (CRUD)
# ==============================================================================

@login_required
def novo_atendimento(request):
    perfil = getattr(request.user, 'perfilusuario', None)
    if not perfil:
        return redirect('dashboard')

    if request.method == 'POST':
        # Passamos request.user para o formulário filtrar clientes/veículos do despachante correto
        form = AtendimentoForm(request.user, request.POST)
        if form.is_valid():
            atendimento = form.save(commit=False)
            
            # 1. Atribui o despachante automaticamente baseado no perfil do usuário logado
            atendimento.despachante = perfil.despachante
            
            # 2. Lógica de Segurança para o Responsável:
            # Se o campo 'responsavel' não estiver no formulário ou vier vazio, 
            # podemos definir o usuário logado como responsável padrão.
            if not atendimento.responsavel:
                atendimento.responsavel = request.user
            
            atendimento.save()
            messages.success(request, "Processo criado com sucesso!")
            return redirect('dashboard')
    else:
        # Inicializa o formulário com o usuário logado como responsável técnico sugerido
        form = AtendimentoForm(request.user, initial={'responsavel': request.user})

    return render(request, 'form_generico.html', {
        'form': form,
        'titulo': 'Novo Processo DETRAN'
    })

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.urls import reverse
# Certifique-se de importar o Atendimento e o Form
from .models import Atendimento
from .forms import AtendimentoForm

@login_required
def editar_atendimento(request, id):
    perfil = request.user.perfilusuario
    despachante = perfil.despachante
    
    # 1. Busca o atendimento (Garante que só edita o que for da própria empresa)
    atendimento = get_object_or_404(Atendimento, id=id, despachante=despachante)
    
    if request.method == 'POST':
        # Passamos request.user conforme seu AtendimentoForm exige no __init__
        form = AtendimentoForm(request.user, request.POST, instance=atendimento)
        
        if form.is_valid():
            # Interceptamos o save para garantir os cálculos de custo
            atendimento_obj = form.save(commit=False)
            
            # --- LÓGICA DE RECALCULO AUTOMÁTICO ---
            # Pegamos o honorário atual (digitado ou vindo do banco)
            h_bruto = atendimento_obj.valor_honorarios or 0
            
            # Aplicamos as alíquotas fixas do Despachante logado
            aliq_imp = despachante.aliquota_imposto / 100
            aliq_ban = despachante.taxa_bancaria_padrao / 100
            
            # Gravamos os custos calculados (proteção contra edição manual)
            atendimento_obj.custo_impostos = h_bruto * aliq_imp
            atendimento_obj.custo_taxa_bancaria = h_bruto * aliq_ban
            
            atendimento_obj.save()
            
            messages.success(request, f"Valores do processo {atendimento_obj.numero_atendimento or id} atualizados!")
            
            # Sugestão: Voltar para o Fluxo de Caixa, que é de onde veio o clique
            return redirect('fluxo_caixa')
    else:
        form = AtendimentoForm(request.user, instance=atendimento)
        
    # --- CORREÇÃO DE SEGURANÇA (MENSAGEM DO MODAL) ---
    info_veiculo = f"do veículo {atendimento.veiculo.placa}" if atendimento.veiculo else "(Sem veículo vinculado)"

    return render(request, 'form_generico.html', {
        'form': form, 
        'titulo': f'Ajustar Valores - Processo {atendimento.numero_atendimento or "S/N"}',
        'url_excluir': reverse('excluir_atendimento', args=[atendimento.id]),
        'texto_modal': f"Tem certeza que deseja excluir o processo {info_veiculo}?",
        'url_voltar': reverse('fluxo_caixa') # Botão voltar leva ao financeiro
    })

@login_required
def detalhe_cliente(request, id):
    perfil = request.user.perfilusuario
    
    # 1. Busca o cliente
    cliente = get_object_or_404(Cliente, id=id, despachante=perfil.despachante)
    
    # 2. Busca os veículos FILTRANDO PELO ID (Igual a API faz)
    # Isso garante que se a API acha, essa tela TAMBÉM tem que achar.
    veiculos = Veiculo.objects.filter(
        cliente_id=cliente.id, 
        despachante=perfil.despachante
    ).order_by('-id')
    
    return render(request, 'clientes/detalhe_cliente.html', {
        'cliente': cliente,
        'veiculos': veiculos
    })

# --- FUNÇÕES DE EXCLUSÃO (SOMENTE ADMIN) ---

@login_required
def excluir_cliente(request, id):
    # 1. Pega o perfil com segurança
    try:
        perfil = request.user.perfilusuario
    except:
        return redirect('dashboard')

    # 2. Busca o cliente (Garante isolamento do escritório)
    cliente = get_object_or_404(Cliente, id=id, despachante=perfil.despachante)

    # 3. VERIFICAÇÃO DUPLA (O Pulo do Gato)
    # Se NÃO for superusuário E TAMBÉM NÃO for Admin, bloqueia.
    if not request.user.is_superuser and perfil.tipo_usuario != 'ADMIN':
        messages.error(request, "⛔ Apenas Administradores podem excluir clientes.")
        return redirect('lista_clientes')

    # 4. Executa a exclusão
    if request.method == 'POST':
        try:
            cliente.delete()
            messages.success(request, "Cliente excluído com sucesso.")
        except Exception:
            messages.error(request, "Erro: Este cliente tem processos vinculados.")
        return redirect('lista_clientes')
    
    return redirect('lista_clientes')

    # 4. Executa a exclusão
    if request.method == 'POST':
        nome = cliente.nome
        try:
            cliente.delete()
            messages.success(request, f"Cliente '{nome}' excluído com sucesso.")
        except Exception as e:
            messages.error(request, "Não é possível excluir este cliente pois ele possui registros vinculados.")
        return redirect('lista_clientes')
    
    return redirect('lista_clientes')

@login_required
def lista_clientes(request):
    perfil = request.user.perfilusuario
    
    # Começa pegando a lista base do despachante
    clientes = Cliente.objects.filter(despachante=perfil.despachante).order_by('nome')
    
    # Verifica se tem termo de busca na URL (ex: ?q=joao)
    search_term = request.GET.get('q')

    if search_term:
        # Filtra onde o termo aparece no Nome, CPF/CNPJ ou Telefone
        clientes = clientes.filter(
            Q(nome__icontains=search_term) | 
            Q(cpf_cnpj__icontains=search_term) |
            Q(telefone__icontains=search_term)
        )

    return render(request, 'clientes/lista_clientes.html', {'clientes': clientes})

@login_required
def excluir_veiculo(request, id):
    try:
        perfil = request.user.perfilusuario
    except:
        return redirect('dashboard')

    veiculo = get_object_or_404(Veiculo, id=id, despachante=perfil.despachante)

    # TRAVA DE SEGURANÇA
    if not request.user.is_superuser and perfil.tipo_usuario != 'ADMIN':
        messages.error(request, "⛔ Apenas Administradores podem excluir veículos.")
        return redirect('lista_clientes')

    if request.method == 'POST':
        veiculo.delete()
        messages.success(request, "Veículo excluído.")
        return redirect('lista_clientes')

    return redirect('lista_clientes')

@login_required
def excluir_atendimento(request, id):
    try:
        perfil = request.user.perfilusuario
    except:
        return redirect('dashboard')

    # Busca o atendimento garantindo que pertence ao escritório do usuário (SaaS)
    atendimento = get_object_or_404(Atendimento, id=id, despachante=perfil.despachante)

    # --- TRAVA REMOVIDA: Agora qualquer funcionário do escritório pode excluir ---
    
    if request.method == 'POST':
        atendimento.delete()
        messages.success(request, "Processo removido com sucesso.")
        return redirect('dashboard')

    return redirect('dashboard')


@login_required
def excluir_atendimento(request, id):
    try:
        perfil = request.user.perfilusuario
    except:
        return redirect('dashboard')

    atendimento = get_object_or_404(Atendimento, id=id, despachante=perfil.despachante)

    # TRAVA DE SEGURANÇA
    if perfil.tipo_usuario != 'ADMIN' and not request.user.is_superuser:
        messages.error(request, "⛔ Permissão Negada: Apenas Administradores podem excluir processos.")
        return redirect('dashboard')

    if request.method == 'POST':
        atendimento.delete()
        messages.success(request, "Processo removido com sucesso.")
        return redirect('dashboard')

    return redirect('dashboard')
# ==============================================================================
# CADASTROS DE BASE (CLIENTES E VEÍCULOS)
# ==============================================================================

@login_required
def novo_cliente(request):
    # Garante que o usuário tem um perfil vinculado
    perfil = getattr(request.user, 'perfilusuario', None)
    if not perfil:
        return redirect('logout')

    if request.method == 'POST':
        try:
            with transaction.atomic():
                despachante = perfil.despachante

                # =========================================================
                # 1. CLIENTE
                # =========================================================
                cpf_cnpj_raw = request.POST.get('cliente_cpf_cnpj', '')
                
                cliente, created = Cliente.objects.get_or_create(
                    cpf_cnpj=cpf_cnpj_raw, 
                    despachante=despachante,
                    defaults={
                        'nome': request.POST.get('cliente_nome'),
                        'telefone': request.POST.get('cliente_telefone'),
                        'email': request.POST.get('cliente_email'),
                        'rg': request.POST.get('rg'),
                        'orgao_expedidor': request.POST.get('orgao_expedidor'),
                        'profissao': request.POST.get('profissao'),
                        
                        # --- NOVOS CAMPOS ADICIONADOS ---
                        'filiacao': request.POST.get('filiacao'),
                        'uf_rg': request.POST.get('uf_rg'),
                        # --------------------------------

                        # Endereço
                        'cep': request.POST.get('cep'),
                        'rua': request.POST.get('rua'),
                        'numero': request.POST.get('numero'),
                        'bairro': request.POST.get('bairro'),
                        'cidade': request.POST.get('cidade', 'Goiânia'),
                        'uf': request.POST.get('uf', 'GO'),
                        'complemento': request.POST.get('complemento'),
                    }
                )

                # Se o cliente já existia, atualizamos os dados
                if not created:
                    cliente.nome = request.POST.get('cliente_nome')
                    cliente.telefone = request.POST.get('cliente_telefone')
                    cliente.email = request.POST.get('cliente_email')
                    
                    # Atualiza os novos campos também
                    cliente.filiacao = request.POST.get('filiacao')
                    cliente.uf_rg = request.POST.get('uf_rg')
                    
                    # Atualiza os outros campos importantes
                    cliente.rg = request.POST.get('rg')
                    cliente.orgao_expedidor = request.POST.get('orgao_expedidor')
                    cliente.rua = request.POST.get('rua')
                    cliente.numero = request.POST.get('numero')
                    cliente.bairro = request.POST.get('bairro')
                    cliente.cidade = request.POST.get('cidade')
                    cliente.uf = request.POST.get('uf')
                    
                    cliente.save()

                # =========================================================
                # 2. VEÍCULOS (Mantido idêntico ao seu código)
                # =========================================================
                placas = request.POST.getlist('veiculo_placa[]')
                renavams = request.POST.getlist('veiculo_renavam[]')
                chassis = request.POST.getlist('veiculo_chassi[]')
                marcas = request.POST.getlist('veiculo_marca[]')
                modelos = request.POST.getlist('veiculo_modelo[]')
                cores = request.POST.getlist('veiculo_cor[]')
                anos_fab = request.POST.getlist('veiculo_ano_fabricacao[]')
                anos_mod = request.POST.getlist('veiculo_ano_modelo[]')
                tipos = request.POST.getlist('veiculo_tipo[]')

                for i in range(len(placas)):
                    # Limpa a placa (remove - e espaço)
                    placa_limpa = placas[i].replace('-', '').replace(' ', '').upper()
                    
                    if not placa_limpa: continue

                    if len(placa_limpa) > 7: placa_limpa = placa_limpa[:7]

                    # Valores padrão para anos vazios
                    af = anos_fab[i] if anos_fab[i] else 2000
                    am = anos_mod[i] if anos_mod[i] else 2000

                    Veiculo.objects.get_or_create(
                        placa=placa_limpa,
                        despachante=despachante,
                        defaults={
                            'cliente': cliente,
                            'renavam': renavams[i],
                            'chassi': chassis[i],
                            'marca': marcas[i],
                            'modelo': modelos[i],
                            'cor': cores[i],
                            'ano_fabricacao': af,
                            'ano_modelo': am,
                            'tipo': tipos[i]
                        }
                    )

            return redirect('dashboard')

        except Exception as e:
            print(f"❌ Erro no Cadastro Cliente: {e}")
            pass

    # Mantive exatamente o nome do template que você usava
    return render(request, 'clientes/cadastro_cliente.html')

@login_required
def novo_veiculo(request):
    perfil = request.user.perfilusuario
    if request.method == 'POST':
        form = VeiculoForm(request.user, request.POST) # Precisa receber user para filtrar clientes
        if form.is_valid():
            veiculo = form.save(commit=False)
            veiculo.despachante = perfil.despachante
            veiculo.save()
            return redirect('dashboard')
    else:
        form = VeiculoForm(request.user)
    
    return render(request, 'form_generico.html', {'form': form, 'titulo': 'Cadastrar Veículo'})


@login_required
def cadastro_rapido(request):
    """
    Tela de lançamento ágil: Busca cliente + Adiciona Veículos + Cria Processos em lote
    Atualizado: Agora calcula lucros e impostos automaticamente via alíquotas do Despachante.
    """
    perfil = getattr(request.user, 'perfilusuario', None)
    if not perfil:
        return redirect('logout')

    despachante = perfil.despachante
    servicos_db = TipoServico.objects.filter(despachante=despachante, ativo=True)
    equipe = PerfilUsuario.objects.filter(despachante=despachante).select_related('user')

    if request.method == 'POST':
        try:
            with transaction.atomic():
                # 1. Responsável Técnico
                responsavel_id = request.POST.get('responsavel_id')
                responsavel_obj = request.user
                if responsavel_id:
                    try:
                        responsavel_obj = User.objects.get(id=responsavel_id)
                    except User.DoesNotExist:
                        pass

                # 2. Cliente
                cliente_id = request.POST.get('cliente_id')
                if not cliente_id:
                    messages.error(request, "Nenhum cliente selecionado.")
                    return redirect('cadastro_rapido')
                
                cliente = get_object_or_404(Cliente, id=cliente_id, despachante=despachante)

                # 3. Captura Listas (Lote)
                placas = request.POST.getlist('veiculo_placa[]')
                modelos = request.POST.getlist('veiculo_modelo[]')
                servicos_str_lista = request.POST.getlist('servico[]') # Vem como "Serviço A + Serviço B"
                atendimentos = request.POST.getlist('numero_atendimento[]')
                
                obs_geral = request.POST.get('observacoes', '')
                prazo_input = request.POST.get('prazo_entrega')

                # 4. Loop de Criação
                for i in range(len(placas)):
                    placa_limpa = placas[i].replace('-', '').replace(' ', '').upper()
                    if not placa_limpa: continue

                    # Garante existência do Veículo
                    veiculo, _ = Veiculo.objects.get_or_create(
                        placa=placa_limpa,
                        despachante=despachante,
                        defaults={'cliente': cliente, 'modelo': modelos[i].upper()}
                    )

                    # --- LÓGICA FINANCEIRA AUTOMATIZADA ---
                    # Quebra a string de serviços para calcular os valores individuais
                    nomes_selecionados = [s.strip() for s in servicos_str_lista[i].split('+')]
                    
                    total_taxas = 0
                    total_honorarios = 0

                    for nome_s in nomes_selecionados:
                        # Busca na tabela de preços do despachante
                        s_base = servicos_db.filter(nome__iexact=nome_s).first()
                        if s_base:
                            total_taxas += s_base.valor_base
                            total_honorarios += s_base.honorarios
                        else:
                            # Se for um serviço digitado manualmente que não está na tabela
                            # (Opcional: você pode definir um valor padrão ou deixar zerado)
                            pass

                    # Aplica as alíquotas fixas do Despachante sobre o total de honorários
                    custo_imp = total_honorarios * (despachante.aliquota_imposto / 100)
                    custo_ban = total_honorarios * (despachante.taxa_bancaria_padrao / 100)

                    # 5. Cria o Atendimento
                    Atendimento.objects.create(
                        despachante=despachante,
                        cliente=cliente,
                        veiculo=veiculo,
                        servico=servicos_str_lista[i],
                        responsavel=responsavel_obj,
                        numero_atendimento=atendimentos[i] if i < len(atendimentos) else '',
                        
                        # Valores Financeiros Provisionados
                        valor_taxas_detran=total_taxas,
                        valor_honorarios=total_honorarios,
                        custo_impostos=custo_imp,
                        custo_taxa_bancaria=custo_ban,
                        status_financeiro='ABERTO',
                        
                        status='SOLICITADO',
                        data_solicitacao=timezone.now().date(),
                        data_entrega=prazo_input if prazo_input else None,
                        observacoes_internas=f"{obs_geral}\nGerado via Cadastro Rápido."
                    )

            messages.success(request, f"{len(placas)} processos criados com cálculos financeiros automáticos!")
            return redirect('dashboard')

        except Exception as e:
            messages.error(request, f"Erro ao processar lote: {e}")
            return redirect('cadastro_rapido')

    return render(request, 'processos/cadastro_rapido.html', {
        'servicos_db': servicos_db,
        'equipe': equipe
    })

# --- API DE BUSCA DE CLIENTES (AUTOCOMPLETE) ---


@login_required
def buscar_clientes(request):
    term = request.GET.get('term', '')
    
    # Garante que o usuário tem perfil e despachante vinculado
    perfil = getattr(request.user, 'perfilusuario', None)
    if not perfil or not perfil.despachante:
        return JsonResponse({'results': []}, safe=False)

    despachante = perfil.despachante
    
    # 1. Filtro Base: Apenas clientes deste despachante
    filters = Q(despachante=despachante)

    if term:
        # Limpa o termo para tentar buscar apenas por números (ex: busca CPF sem ponto)
        term_limpo = re.sub(r'\D', '', term) 

        # 2. Monta a query PODEROSA:
        filters = filters & (
            Q(nome__icontains=term) | 
            Q(cpf_cnpj__icontains=term) | 
            Q(telefone__icontains=term) |
            # AQUI ESTAVA O ERRO: Mudamos de 'veiculo' para 'veiculos' (plural)
            Q(veiculos__placa__icontains=term)  
        )
        
        if term_limpo:
             # Adiciona busca extra pelo CPF limpo se houver números
             filters |= Q(cpf_cnpj__icontains=term_limpo)

    # 3. Executa a Query
    # .distinct() é obrigatório aqui para não repetir o cliente se ele tiver 2 carros que batem na busca
    clientes = Cliente.objects.filter(filters).distinct().order_by('nome')[:20]

    results = []
    for c in clientes:
        # Formatação bonita para o Select2
        text_display = f"{c.nome.upper()} - {c.cpf_cnpj}"
        
        results.append({
            'id': c.id,
            'text': text_display  # O Select2 usa este campo 'text' para exibir na lista
        })
    
    return JsonResponse({'results': results}, safe=False)

@login_required
def api_veiculos_cliente(request, cliente_id):
    # Garante segurança: só veículos do despachante logado
    despachante = request.user.perfilusuario.despachante
    
    veiculos = Veiculo.objects.filter(cliente_id=cliente_id, despachante=despachante)
    
    data = []
    for v in veiculos:
        data.append({
            'id': v.id,
            'placa': v.placa,
            'modelo': v.modelo,
            'renavam': v.renavam or '',
            'marca': v.marca or '',
            'cor': v.cor,
            'ano_fab': v.ano_fabricacao,
            'ano_mod': v.ano_modelo,
            'tipo': v.tipo
        })
    
    return JsonResponse(data, safe=False)


@login_required
def editar_cliente(request, id):
    perfil = request.user.perfilusuario
    cliente = get_object_or_404(Cliente, id=id, despachante=perfil.despachante)
    
    if request.method == 'POST':
        form = ClienteForm(request.POST, instance=cliente) # instance preenche os dados atuais
        if form.is_valid():
            form.save()
            return redirect('lista_clientes') # Redirecionar para a lista faz mais sentido agora, mas pode ser 'dashboard' se preferir
    else:
        form = ClienteForm(instance=cliente)

    # CORREÇÃO AQUI: Apontando para o template novo de colunas
    return render(request, 'clientes/editar_cliente.html', {
        'form': form
    })

@login_required
def editar_veiculo(request, id):
    perfil = getattr(request.user, 'perfilusuario', None)
    veiculo = get_object_or_404(Veiculo, id=id, despachante=perfil.despachante)

    if request.method == 'POST':
        # Passamos request.user pois seu VeiculoForm filtra os clientes no __init__
        form = VeiculoForm(request.user, request.POST, instance=veiculo)
        if form.is_valid():
            form.save()
            return redirect('dashboard') # Ou redirecionar para 'detalhe_cliente' do dono do carro
    else:
        form = VeiculoForm(request.user, instance=veiculo)

    # CORREÇÃO AQUI: Apontando para o template novo de veículo
    return render(request, 'veiculos/editar_veiculo.html', {
        'form': form
    })

# --- GESTÃO DE SERVIÇOS E VALORES ---

@login_required
def gerenciar_servicos(request):
    perfil = request.user.perfilusuario
    servicos = TipoServico.objects.filter(despachante=perfil.despachante, ativo=True)
    
    if request.method == 'POST':
        nome = request.POST.get('nome')
        
        # Captura os valores brutos do formulário
        raw_base = request.POST.get('valor_base')
        raw_hon = request.POST.get('honorarios')

        # 1. Tratamento do Valor Base (Taxas)
        if raw_base:
            v_base = raw_base.replace(',', '.')
        else:
            v_base = 0  # Se estiver vazio, salva 0.00

        # 2. Tratamento dos Honorários
        if raw_hon:
            v_hon = raw_hon.replace(',', '.')
        else:
            v_hon = 0   # Se estiver vazio, salva 0.00
        
        # Criação segura no banco
        TipoServico.objects.create(
            despachante=perfil.despachante,
            nome=nome,
            valor_base=v_base,
            honorarios=v_hon
        )
        return redirect('gerenciar_servicos')

    return render(request, 'gerenciar_servicos.html', {'servicos': servicos})

@login_required
def excluir_servico(request, id):
    try:
        perfil = request.user.perfilusuario
    except:
        return redirect('dashboard')

    servico = get_object_or_404(TipoServico, id=id, despachante=perfil.despachante)
    
    # TRAVA DE SEGURANÇA
    if perfil.tipo_usuario != 'ADMIN' and not request.user.is_superuser:
        messages.error(request, "⛔ Permissão Negada: Apenas Administradores podem gerenciar serviços.")
        return redirect('gerenciar_servicos')

    servico.ativo = False # Soft delete
    servico.save()
    messages.success(request, "Serviço removido da lista.")
    return redirect('gerenciar_servicos')

# --- ORÇAMENTOS ---

@login_required
def novo_orcamento(request):
    perfil = request.user.perfilusuario
    servicos_disponiveis = TipoServico.objects.filter(despachante=perfil.despachante, ativo=True)
    
    if request.method == 'POST':
        try:
            with transaction.atomic():
                # --- FUNÇÃO DE LIMPEZA DE VALOR ---
                def limpar_valor(valor):
                    if not valor: return 0.0
                    v = str(valor).strip()
                    if ',' in v:
                        v = v.replace('.', '').replace(',', '.')
                    return float(v)

                # 1. Captura e Limpa os Valores
                desconto = limpar_valor(request.POST.get('desconto'))
                valor_total = limpar_valor(request.POST.get('valor_total_hidden'))
                
                # 2. Dados Básicos (Incluindo VEÍCULO agora)
                cliente_id = request.POST.get('cliente_id')
                nome_avulso = request.POST.get('cliente_nome_avulso')
                observacoes = request.POST.get('observacoes')
                
                # --- NOVO: Captura o Veículo ---
                veiculo_id = request.POST.get('veiculo_id')
                veiculo_obj = None
                if veiculo_id:
                    # Busca o veículo garantindo que ele existe
                    veiculo_obj = Veiculo.objects.filter(id=veiculo_id).first()

                # 3. Cria o Orçamento
                orcamento = Orcamento.objects.create(
                    despachante=perfil.despachante,
                    observacoes=observacoes,
                    desconto=desconto,
                    valor_total=valor_total,
                    status='PENDENTE',
                    
                    # --- NOVO: Salva o vínculo com o veículo ---
                    veiculo=veiculo_obj 
                )

                # 4. Vincula Cliente
                if cliente_id:
                    orcamento.cliente = Cliente.objects.filter(id=cliente_id).first()
                elif nome_avulso:
                    orcamento.nome_cliente_avulso = nome_avulso.upper()
                
                orcamento.save()

                # 5. Salva os Itens
                ids_servicos = request.POST.getlist('servicos[]') 
                precos_servicos = request.POST.getlist('precos[]')
                
                if not ids_servicos:
                    raise Exception("A lista de serviços está vazia.")

                for servico_id, preco_raw in zip(ids_servicos, precos_servicos):
                    servico_obj = TipoServico.objects.filter(id=servico_id).first()
                    nome_servico = servico_obj.nome if servico_obj else "Serviço Avulso"
                    
                    valor_item = limpar_valor(preco_raw)

                    ItemOrcamento.objects.create(
                        orcamento=orcamento,
                        servico_nome=nome_servico,
                        valor=valor_item
                    )

                messages.success(request, f"Orçamento #{orcamento.id} gerado com sucesso!")
                return redirect('detalhe_orcamento', id=orcamento.id)

        except Exception as e:
            messages.error(request, f"Erro ao criar orçamento: {e}")
            return redirect('novo_orcamento')

    return render(request, 'financeiro/novo_orcamento.html', {'servicos': servicos_disponiveis})
# views.py

@login_required
def detalhe_orcamento(request, id):
    # Busca o orçamento garantindo que pertence ao despachante logado
    orcamento = get_object_or_404(Orcamento, id=id, despachante=request.user.perfilusuario.despachante)
    
    return render(request, 'financeiro/detalhe_orcamento.html', {'orcamento': orcamento})

# views.py

@login_required
def aprovar_orcamento(request, id):
    # 1. Busca o orçamento e os dados do despachante (alíquotas)
    orcamento = get_object_or_404(Orcamento, id=id, despachante=request.user.perfilusuario.despachante)
    despachante = request.user.perfilusuario.despachante

    # 2. Verificações de segurança
    if orcamento.status == 'APROVADO':
        messages.warning(request, "Este orçamento já foi aprovado.")
        return redirect('detalhe_orcamento', id=id)

    # Lógica de Cliente Avulso (Cria cadastro se não existir - Mantido)
    if not orcamento.cliente and orcamento.nome_cliente_avulso:
        try:
            novo_cliente = Cliente.objects.create(
                despachante=orcamento.despachante,
                nome=orcamento.nome_cliente_avulso.upper(),
                observacoes="Criado automaticamente via Aprovação de Orçamento."
            )
            orcamento.cliente = novo_cliente
            orcamento.save()
        except Exception as e:
            messages.error(request, f"Erro ao cadastrar cliente avulso: {e}")
            return redirect('detalhe_orcamento', id=id)

    if not orcamento.cliente:
        messages.error(request, "Erro: Cliente não vinculado.")
        return redirect('detalhe_orcamento', id=id)

    # 3. TRANSFORMAÇÃO (COM CÁLCULOS AUTOMÁTICOS DE ALÍQUOTAS)
    try:
        with transaction.atomic():
            orcamento.status = 'APROVADO'
            orcamento.save()

            total_taxas_detran = 0
            total_honorarios_brutos = 0
            lista_nomes_servicos = []
            detalhes_itens = []

            for item in orcamento.itens.all():
                lista_nomes_servicos.append(item.servico_nome)
                detalhes_itens.append(f"- {item.servico_nome}: R$ {item.valor}")
                
                servico_base = TipoServico.objects.filter(
                    despachante=orcamento.despachante, 
                    nome=item.servico_nome
                ).first()

                if servico_base:
                    total_taxas_detran += servico_base.valor_base
                    total_honorarios_brutos += servico_base.honorarios
                else:
                    total_honorarios_brutos += item.valor

            # --- CÁLCULO AUTOMÁTICO VIA CONFIGURAÇÃO DO DESPACHANTE ---
            # Aqui usamos as alíquotas que adicionamos no modelo Despachante
            valor_impostos = total_honorarios_brutos * (despachante.aliquota_imposto / 100)
            valor_taxa_bancaria = total_honorarios_brutos * (despachante.taxa_bancaria_padrao / 100)

            nome_servico_agrupado = " + ".join(lista_nomes_servicos)[:100]
            obs_final = f"Gerado via Orçamento #{orcamento.id}.\nItens:\n" + "\n".join(detalhes_itens) + f"\n\nObs: {orcamento.observacoes or ''}"

            # 4. Cria o Atendimento
            Atendimento.objects.create(
                despachante=orcamento.despachante,
                cliente=orcamento.cliente,
                veiculo=orcamento.veiculo,
                servico=nome_servico_agrupado,
                
                # --- FINANCEIRO AUTOMATIZADO ---
                valor_taxas_detran=total_taxas_detran,
                valor_honorarios=total_honorarios_brutos,
                custo_impostos=valor_impostos,         # Preenchido via alíquota configurada
                custo_taxa_bancaria=valor_taxa_bancaria, # Preenchido via alíquota configurada
                status_financeiro='ABERTO', 
                quem_pagou_detran='DESPACHANTE',
                # --------------------------------
                
                status='SOLICITADO',
                data_solicitacao=timezone.now().date(),
                observacoes_internas=obs_final
            )

        messages.success(request, f"Orçamento Aprovado! Custos operacionais ({despachante.aliquota_imposto}% imposto) calculados automaticamente.")
        return redirect('dashboard')

    except Exception as e:
        messages.error(request, f"Erro ao gerar processo: {e}")
        return redirect('detalhe_orcamento', id=id)

@login_required
def listar_orcamentos(request):
    # 1. Pega os parâmetros
    termo = request.GET.get('termo', '').strip()
    status_filtro = request.GET.get('status')
    
    # 2. Segurança
    perfil = getattr(request.user, 'perfilusuario', None)
    if not perfil:
        return redirect('logout')

    # 3. Query Base Otimizada
    # Adicionamos 'veiculo' no select_related e 'itens' no prefetch_related
    orcamentos = Orcamento.objects.filter(
        despachante=perfil.despachante
    ).select_related('cliente', 'veiculo').prefetch_related('itens').order_by('-data_criacao')
    
    # 4. Filtro de Busca (Agora busca Placa também!)
    if termo:
        filtros = (
            Q(cliente__nome__icontains=termo) |          # Nome do Cliente
            Q(cliente__cpf_cnpj__icontains=termo) |      # CPF/CNPJ
            Q(nome_cliente_avulso__icontains=termo) |    # Nome Avulso
            
            # --- NOVO: Busca por Veículo ---
            Q(veiculo__placa__icontains=termo) |         # Placa
            Q(veiculo__modelo__icontains=termo)          # Modelo do carro
        )

        if termo.isdigit():
            filtros |= Q(id=termo)

        orcamentos = orcamentos.filter(filtros)
    
    # 5. Filtro de Status
    if status_filtro:
        orcamentos = orcamentos.filter(status=status_filtro)
        
    return render(request, 'financeiro/lista_orcamentos.html', {
        'orcamentos': orcamentos,
        'filters': request.GET 
    })

@login_required
def excluir_orcamento(request, id):
    try:
        perfil = request.user.perfilusuario
    except:
        return redirect('dashboard')

    orcamento = get_object_or_404(Orcamento, id=id, despachante=perfil.despachante)
    
    # --- NOVA TRAVA DE SEGURANÇA ---
    # Se o usuário NÃO for Admin/Dono, verificamos o status.
    if not request.user.is_superuser and perfil.tipo_usuario != 'ADMIN':
        if orcamento.status == 'APROVADO':
            messages.error(request, "⛔ Permissão Negada: Operadores não podem excluir orçamentos já APROVADOS.")
            return redirect('listar_orcamentos')

    if request.method == 'POST':
        orcamento.delete()
        messages.success(request, f"Orçamento #{id} excluído com sucesso.")
        return redirect('listar_orcamentos')
    
    return redirect('listar_orcamentos')


@login_required
def relatorio_mensal(request):
    """
    Relatório de Produção: Filtra processos por intervalo de datas, 
    cliente/placa e operador responsável.
    """
    despachante = request.user.perfilusuario.despachante
    
    # 1. CAPTURA DOS NOVOS FILTROS DE DATA
    # Padrão: Se não informar datas, traz o mês atual (do dia 01 até hoje)
    hoje = timezone.now().date()
    data_inicio_padrao = hoje.replace(day=1).strftime('%Y-%m-%d')
    data_fim_padrao = hoje.strftime('%Y-%m-%d')

    data_inicio = request.GET.get('data_inicio', data_inicio_padrao)
    data_fim = request.GET.get('data_fim', data_fim_padrao)
    cliente_placa = request.GET.get('cliente_placa')
    responsavel_id = request.GET.get('responsavel')

    # 2. QUERY BASE
    processos = Atendimento.objects.filter(
        despachante=despachante
    ).select_related('cliente', 'veiculo', 'responsavel').order_by('-data_solicitacao')

    # 3. APLICAÇÃO DINÂMICA DOS FILTROS
    if data_inicio and data_fim:
        processos = processos.filter(data_solicitacao__range=[data_inicio, data_fim])
    
    if cliente_placa:
        processos = processos.filter(
            Q(cliente__nome__icontains=cliente_placa) | 
            Q(veiculo__placa__icontains=cliente_placa)
        )
    
    if responsavel_id:
        processos = processos.filter(responsavel_id=responsavel_id)

    # 4. RESUMO POR STATUS (Agrupamento limpo para evitar duplicados)
    resumo_raw = processos.values('status').annotate(total=Count('id'))
    status_dict = dict(Atendimento.STATUS_CHOICES)
    
    resumo_status = []
    for item in resumo_raw:
        resumo_status.append({
            'status': status_dict.get(item['status'], item['status']),
            'total': item['total']
        })

    # 5. DADOS COMPLEMENTARES
    equipe = PerfilUsuario.objects.filter(despachante=despachante).select_related('user')

    context = {
        'processos': processos,
        'equipe': equipe,
        'resumo_status': resumo_status,
        'total_qtd': processos.count(),
        'filtros': {
            'data_inicio': data_inicio,
            'data_fim': data_fim,
            'cliente_placa': cliente_placa,
            'responsavel': responsavel_id
        }
    }
    
    return render(request, 'cadastro/relatorio_mensal.html', context)


@login_required
def relatorio_servicos(request):
    """
    Gera o extrato de serviços por cliente.
    Otimizado para carregar dados apenas sob demanda.
    """
    # 1. Captura de Filtros
    data_inicio = request.GET.get('data_inicio')
    data_fim = request.GET.get('data_fim')
    cliente_placa = request.GET.get('cliente_placa')
    status_fin = request.GET.get('status_financeiro')

    # Iniciamos as variáveis de retorno vazias
    relatorio_agrupado = None
    total_geral_taxas = 0
    total_geral_honorarios = 0
    total_geral_valor = 0

    # 2. LÓGICA DE PERFORMANCE: Só acessa o banco se houver busca
    if cliente_placa:
        # Busca base: Mantido 'APROVADO' (Serviços prontos/entregues)
        atendimentos = Atendimento.objects.filter(
            despachante=request.user.perfilusuario.despachante,
            status='APROVADO' 
        ).select_related('cliente', 'veiculo').order_by('cliente__nome', '-data_solicitacao')

        # Filtros Opcionais
        if data_inicio:
            atendimentos = atendimentos.filter(data_solicitacao__gte=data_inicio)
        if data_fim:
            atendimentos = atendimentos.filter(data_solicitacao__lte=data_fim)
        if status_fin:
            atendimentos = atendimentos.filter(status_financeiro=status_fin)

        # Filtro de Texto (Nome, Placa)
        atendimentos = atendimentos.filter(
            Q(cliente__nome__icontains=cliente_placa) |
            Q(veiculo__placa__icontains=cliente_placa)
        )

        # 3. Agrupamento e Construção de Dados
        relatorio_agrupado = {}
        
        for item in atendimentos:
            taxas = item.valor_taxas_detran or 0
            honorarios = item.valor_honorarios or 0
            valor_total_item = taxas + honorarios
            
            # Dados auxiliares
            placa = item.veiculo.placa if item.veiculo else "S/P"
            modelo = item.veiculo.modelo if item.veiculo else "---"

            cliente_id = item.cliente.id
            if cliente_id not in relatorio_agrupado:
                # --- LIMPEZA DE TELEFONE (Evita Erro 404 no WhatsApp) ---
                tel_bruto = item.cliente.telefone or ""
                tel_limpo = "".join([c for c in tel_bruto if c.isdigit()])

                relatorio_agrupado[cliente_id] = {
                    'dados_cliente': item.cliente,
                    'telefone_limpo': tel_limpo, # Vai para o botão do template
                    'itens': [],      
                    'linhas_zap': [], 
                    'texto_whatsapp': '', 
                    'subtotal_taxas': 0,
                    'subtotal_honorarios': 0,
                    'subtotal_valor': 0
                }

            # --- ADICIONA DADOS PARA A TABELA ---
            relatorio_agrupado[cliente_id]['itens'].append({
                'id': item.id, # <--- IMPORTANTE: Necessário para o botão de Imprimir Recibo
                'data': item.data_solicitacao,
                'placa': placa,
                'modelo': modelo,
                'numero_atendimento': item.numero_atendimento,
                'servico_nome': item.servico,
                'taxas': taxas,
                'honorario': honorarios,
                'valor_total': valor_total_item,
                'status_fin': item.get_status_financeiro_display()
            })

            # --- MONTA LINHA PARA O WHATSAPP ---
            # Ex: "• Transferência (ABC-1234) - R$ 500,00"
            linha_formatada = f"• {item.servico} ({placa}) - R$ {valor_total_item:.2f}"
            relatorio_agrupado[cliente_id]['linhas_zap'].append(linha_formatada)

            # --- ATUALIZA TOTAIS ---
            relatorio_agrupado[cliente_id]['subtotal_taxas'] += taxas
            relatorio_agrupado[cliente_id]['subtotal_honorarios'] += honorarios
            relatorio_agrupado[cliente_id]['subtotal_valor'] += valor_total_item

            total_geral_taxas += taxas
            total_geral_honorarios += honorarios
            total_geral_valor += valor_total_item

        # 4. Pós-Processamento: Finalizar texto do WhatsApp
        for c_id, dados in relatorio_agrupado.items():
            nome_cliente = dados['dados_cliente'].nome.split()[0] if dados['dados_cliente'].nome else "Cliente"
            total_formatado = f"{dados['subtotal_valor']:.2f}"
            
            lista_servicos = "\n".join(dados['linhas_zap'])
            
            msg = (
                f"Olá {nome_cliente}, segue o extrato dos seus serviços:\n\n"
                f"{lista_servicos}\n\n"
                f"*TOTAL A PAGAR: R$ {total_formatado}*"
            )
            
            dados['texto_whatsapp'] = msg

    context = {
        'relatorio_agrupado': relatorio_agrupado,
        'total_geral_taxas': total_geral_taxas,
        'total_geral_honorarios': total_geral_honorarios,
        'total_geral_valor': total_geral_valor,
        'filtros': request.GET
    }
    # Caminho corrigido para o template
    return render(request, 'relatorios/relatorio_servicos.html', context)

@login_required
def selecao_documento(request):
    # Agora: Filtramos apenas pelo despachante logado
    despachante_logado = request.user.perfilusuario.despachante
    
    clientes = Cliente.objects.filter(despachante=despachante_logado).order_by('nome')
    servicos = TipoServico.objects.filter(despachante=despachante_logado, ativo=True)
    
    return render(request, 'documentos/selecao_documento.html', {
        'clientes': clientes, 
        'servicos': servicos
    })

@login_required
def imprimir_documento(request):
    if request.method == 'POST':
        # 1. Coleta os dados básicos do formulário
        tipo_doc = request.POST.get('tipo_documento')
        cliente_id = request.POST.get('cliente_id')
        veiculo_placa = request.POST.get('veiculo_placa')
        
        servicos_selecionados_ids = request.POST.getlist('servicos_selecionados')
        motivo_2via = request.POST.get('motivo_2via')
        alteracao_pretendida = request.POST.get('alteracao_pretendida')
        valor_recibo = request.POST.get('valor_recibo')
        
        # Dados para lógica do Outorgado (Procuração Particular e Baixa)
        tipo_outorgado = request.POST.get('tipo_outorgado') 
        outorgado_id = request.POST.get('outorgado_id')

        # Campos ATPV-e
        comprador_id = request.POST.get('comprador_id')
        valor_venda = request.POST.get('valor_venda')
        numero_crv = request.POST.get('numero_crv')
        numero_atpv = request.POST.get('numero_atpv')

        # Campos para Baixa de Veículo
        motivo_baixa = request.POST.get('motivo_baixa')
        tipo_solicitante_baixa = request.POST.get('tipo_solicitante_baixa')
        possui_procurador_baixa = request.POST.get('possui_procurador_baixa') 

        despachante_obj = request.user.perfilusuario.despachante

        # 3. BUSCA SEGURA DO CLIENTE
        cliente = get_object_or_404(
            Cliente, 
            id=cliente_id, 
            despachante=despachante_obj
        )
        
        # 4. BUSCA DO VEÍCULO
        veiculo = None
        if veiculo_placa:
            veiculo = Veiculo.objects.filter(placa=veiculo_placa, cliente=cliente).first()

        # 5. LÓGICA DO OUTORGADO
        outorgado_dados = {}
        
        docs_com_procurador = ['procuracao_particular', 'requerimento_baixa']

        if tipo_doc in docs_com_procurador and tipo_outorgado == 'outro' and outorgado_id:
            try:
                pessoa = Cliente.objects.get(id=outorgado_id, despachante=despachante_obj)
                outorgado_dados = _formatar_dados_pessoa(pessoa)
            except Cliente.DoesNotExist:
                outorgado_dados = _dados_do_escritorio(despachante_obj)
        else:
            outorgado_dados = _dados_do_escritorio(despachante_obj)

        # 6. LÓGICA DO COMPRADOR
        comprador_dados = {}
        if tipo_doc == 'procuracao_atpv' and comprador_id:
            try:
                comp = Cliente.objects.get(id=comprador_id, despachante=despachante_obj)
                comprador_dados = _formatar_dados_pessoa(comp)
            except Cliente.DoesNotExist:
                comprador_dados = {'nome': 'COMPRADOR NÃO ENCONTRADO'}

        # --- [NOVO] PROCESSAMENTO DAS FOTOS (Para Termos Fotográficos) ---
        fotos_processadas = []
        for i in range(1, 5): # Loop de 1 a 4 (foto1, foto2, foto3, foto4)
            campo_foto = f'foto{i}'
            if campo_foto in request.FILES:
                # Converte a imagem enviada para Base64 para exibir no HTML
                img_b64 = _imagem_para_base64(request.FILES[campo_foto])
                fotos_processadas.append(img_b64)
            else:
                fotos_processadas.append(None)
        # -----------------------------------------------------------------

        # 7. FORMATAÇÃO DOS SERVIÇOS
        lista_nomes_servicos = []
        if servicos_selecionados_ids:
            servicos_objs = TipoServico.objects.filter(id__in=servicos_selecionados_ids)
            lista_nomes_servicos = [s.nome for s in servicos_objs]
        
        texto_servicos = ", ".join(lista_nomes_servicos) if lista_nomes_servicos else "______________________________________________________"

        # 8. CONTEXTO GERAL
        context = {
            'cliente': cliente,
            'veiculo': veiculo,
            'despachante': despachante_obj,
            'outorgado': outorgado_dados,
            'hoje': timezone.now(),
            'servicos_solicitados': texto_servicos,
            'motivo_2via': motivo_2via,
            'alteracao_pretendida': alteracao_pretendida,
            'valor_recibo': valor_recibo,
            'comprador': comprador_dados,
            'transacao': {
                'valor': valor_venda,
                'crv': numero_crv,
                'atpv': numero_atpv
            },
            'motivo_baixa': motivo_baixa,
            'tipo_solicitante_baixa': tipo_solicitante_baixa,
            'possui_procurador_baixa': possui_procurador_baixa,
            'fotos': fotos_processadas # [NOVO] Adiciona as fotos ao contexto
        }

        # 9. SELEÇÃO DO DOCUMENTO
        if tipo_doc == 'procuracao':
            return render(request, 'documentos/print_procuracao.html', context)
        elif tipo_doc == 'procuracao_atpv':
            return render(request, 'documentos/print_procuracao_atpv.html', context)
        elif tipo_doc == 'procuracao_particular':
            return render(request, 'documentos/print_procuracao_particular.html', context)
        elif tipo_doc == 'declaracao':
            return render(request, 'documentos/print_declaracao.html', context)
        elif tipo_doc == 'requerimento_2via': 
            return render(request, 'documentos/print_requerimento_2via.html', context)
        elif tipo_doc == 'alteracao_caracteristica':
            return render(request, 'documentos/print_alteracao_caracteristica.html', context)
        elif tipo_doc == 'recibo':
            return render(request, 'documentos/print_recibo.html', context)
        elif tipo_doc == 'contrato':
            return render(request, 'documentos/print_contrato.html', context)    
        elif tipo_doc == 'alteracao_endereco': 
            return render(request, 'documentos/print_alteracao_endereco.html', context)
        elif tipo_doc == 'requerimento_baixa':
            return render(request, 'documentos/print_requerimento_baixa.html', context)
        
        # --- [NOVAS ROTAS] ---
        elif tipo_doc == 'termo_fotografico_veiculo':
            return render(request, 'documentos/print_termo_fotografico_veiculo.html', context)
        elif tipo_doc == 'termo_fotografico_placas':
            return render(request, 'documentos/print_termo_fotografico_placas.html', context)
    
    return redirect('selecao_documento')

# --- Funções Auxiliares ---

def _dados_do_escritorio(despachante):
    return {
        'nome': despachante.nome_fantasia.upper(),
        'doc': f"CNPJ: {despachante.cnpj} | Código: {despachante.codigo_sindego}",
        'endereco': despachante.endereco_completo,
        'cidade': "Goiânia",
        'uf': "GO",
        'telefone': despachante.telefone
    }

def _formatar_dados_pessoa(pessoa):
    # Função que você provavelmente já tem no seu código original,
    # mantive a chamada para não quebrar a lógica do Outorgado/Comprador.
    return {
        'nome': pessoa.nome.upper(),
        'cpf_cnpj': pessoa.cpf_cnpj,
        'rg': pessoa.rg,
        'endereco': f"{pessoa.rua}, {pessoa.numero} {pessoa.complemento} - {pessoa.bairro}",
        'cidade': pessoa.cidade,
        'uf': pessoa.uf
    }

def _imagem_para_base64(imagem_upload):
    """
    Lê o arquivo de imagem enviado pelo formulário e retorna 
    uma string base64 pronta para ser usada na tag <img> do HTML.
    """
    try:
        if not imagem_upload: return None
        imagem_bytes = imagem_upload.read()
        imagem_b64 = base64.b64encode(imagem_bytes).decode('utf-8')
        return f"data:{imagem_upload.content_type};base64,{imagem_b64}"
    except:
        return None

# --- Funções Auxiliares ---

def _dados_do_escritorio(despachante):
    return {
        'nome': despachante.nome_fantasia.upper(),
        'doc': f"CNPJ: {despachante.cnpj} | Código: {despachante.codigo_sindego}",
        'endereco': despachante.endereco_completo,
        'cidade': "Goiânia",
        'uf': "GO",
        'telefone': despachante.telefone
    }

def _imagem_para_base64(imagem_upload):
    """
    Lê o arquivo de imagem enviado pelo formulário e retorna 
    uma string base64 pronta para ser usada na tag <img> do HTML.
    """
    try:
        if not imagem_upload: return None
        # Lê os bytes da imagem
        imagem_bytes = imagem_upload.read()
        # Converte para base64
        imagem_b64 = base64.b64encode(imagem_bytes).decode('utf-8')
        # Retorna formatado para HTML
        return f"data:{imagem_upload.content_type};base64,{imagem_b64}"
    except:
        return None
        
def _formatar_dados_pessoa(pessoa):
    """Padroniza os dados de Cliente para serem usados como Outorgado ou Comprador"""
    return {
        'nome': pessoa.nome.upper(),
        'cpf_cnpj': pessoa.cpf_cnpj, # Usei chaves genéricas para facilitar no HTML
        'doc': pessoa.cpf_cnpj,      # Mantive compatibilidade se algum template usa .doc
        'rg': f"{pessoa.rg or ''} {pessoa.orgao_expedidor or ''}",
        'endereco': f"{pessoa.rua}, {pessoa.numero}, {pessoa.bairro}",
        'cidade': pessoa.cidade,
        'uf': pessoa.uf,
        'cep': pessoa.cep,
        'email': pessoa.email,
        'telefone': pessoa.telefone
    }

@login_required
def ferramentas_compressao(request):
    if request.method == 'POST':
        form = CompressaoPDFForm(request.POST, request.FILES)
        
        if form.is_valid():
            arquivo = request.FILES['arquivo_pdf']
            
            # Chama a função do utils.py (que agora usa a biblioteca PyMuPDF/Fitz)
            pdf_pronto = comprimir_pdf_memoria(arquivo)

            if pdf_pronto:
                # FileResponse entende objetos BytesIO perfeitamente
                return FileResponse(
                    pdf_pronto, 
                    as_attachment=True, 
                    filename=f"Otimizado_{arquivo.name}"
                )
            else:
                messages.error(request, "Não foi possível comprimir este arquivo. Verifique se ele não está protegido por senha.")
    else:
        form = CompressaoPDFForm()

    return render(request, 'ferramentas/compressao.html', {'form': form})

# ==============================================================================
#  ÁREA MASTER (Gestão SaaS - Exclusiva do Superusuário)
# ==============================================================================

def is_master(user):
    """Verifica se o usuário é Superusuário (Dono do Sistema)"""
    return user.is_superuser

# ------------------------------------------------------------------------------
# 1. PAINEL FINANCEIRO (Dashboard do Dono)
# ------------------------------------------------------------------------------
@login_required
@user_passes_test(is_master)
def financeiro_master(request):
    """
    Painel de Controle Financeiro exclusivo para o Dono do Software.
    Mostra lista de clientes, status de pagamento e ações rápidas.
    """
    despachantes = Despachante.objects.all().order_by('nome_fantasia')
    
    lista_financeira = []
    total_receita_mensal = 0
    total_inadimplentes = 0
    
    for d in despachantes:
        # Pega o usuário ADMIN deste despachante para checar validade
        admin_user = d.funcionarios.filter(tipo_usuario='ADMIN').first()
        dias_restantes = admin_user.get_dias_restantes() if admin_user else 0
        
        status_cor = 'success'
        status_texto = 'Em Dia'
        
        if dias_restantes is None:
            status_texto = 'Vitalício'
            status_cor = 'primary'
        elif dias_restantes < 0:
            status_texto = f'VENCIDO ({abs(dias_restantes)} dias)'
            status_cor = 'danger'
            total_inadimplentes += 1
        elif dias_restantes <= 5:
            status_texto = f'Vence logo ({dias_restantes} dias)'
            status_cor = 'warning'
            
        lista_financeira.append({
            'obj': d,
            'admin_nome': admin_user.user.first_name if (admin_user and admin_user.user.first_name) else 'Sem Nome',
            'email_admin': admin_user.user.email if admin_user else '',
            'validade': dias_restantes,
            'status_html': status_texto,
            'cor': status_cor,
            'valor': d.valor_mensalidade
        })
        
        total_receita_mensal += d.valor_mensalidade

    context = {
        'lista_clientes': lista_financeira,
        'total_receita': total_receita_mensal,
        'total_clientes': despachantes.count(),
        'total_inadimplentes': total_inadimplentes,
        'hoje': timezone.now().date()
    }
    
    return render(request, 'financeiro/painel_master.html', context)

# ------------------------------------------------------------------------------
# 2. AÇÕES FINANCEIRAS RÁPIDAS
# ------------------------------------------------------------------------------
@login_required
@user_passes_test(is_master)
def acao_cobrar_cliente(request, despachante_id):
    """Gera boleto no Asaas imediatamente."""
    despachante = get_object_or_404(Despachante, id=despachante_id)
    resultado = gerar_boleto_asaas(despachante)
    
    if resultado['sucesso']:
        messages.success(request, f"Cobrança gerada para {despachante.nome_fantasia}. Link: {resultado['link_fatura']}")
    else:
        messages.error(request, f"Erro ao cobrar: {resultado.get('erro')}")
        
    return redirect('financeiro_master')

@login_required
@user_passes_test(is_master)
def acao_liberar_acesso(request, despachante_id):
    """Dá 20 dias de acesso extra (Cortesia/Desbloqueio)."""
    despachante = get_object_or_404(Despachante, id=despachante_id)
    funcionarios = PerfilUsuario.objects.filter(despachante=despachante)
    hoje = timezone.now().date()
    
    count = 0
    for perfil in funcionarios:
        if not perfil.data_expiracao or perfil.data_expiracao < hoje:
            perfil.data_expiracao = hoje + timedelta(days=20)
        else:
            perfil.data_expiracao = perfil.data_expiracao + timedelta(days=20)
        perfil.save()
        count += 1
        
    messages.success(request, f"Acesso liberado por +20 dias para {despachante.nome_fantasia} ({count} usuários).")
    return redirect('financeiro_master')

# ------------------------------------------------------------------------------
# 3. GESTÃO DE DESPACHANTES (CRUD SEM ADMIN)
# ------------------------------------------------------------------------------
@login_required
@user_passes_test(is_master)
def master_listar_despachantes(request):
    despachantes = Despachante.objects.all().order_by('nome_fantasia')
    return render(request, 'master/lista_despachantes.html', {'despachantes': despachantes})

@login_required
@user_passes_test(is_master)
def master_editar_despachante(request, id=None):
    if id:
        despachante = get_object_or_404(Despachante, id=id)
        titulo = f"Editar: {despachante.nome_fantasia}"
    else:
        despachante = None
        titulo = "Novo Despachante"

    if request.method == 'POST':
        form = DespachanteForm(request.POST, instance=despachante)
        if form.is_valid():
            form.save()
            messages.success(request, "Dados do despachante salvos com sucesso!")
            return redirect('master_listar_despachantes')
    else:
        form = DespachanteForm(instance=despachante)

    return render(request, 'master/form_despachante.html', {'form': form, 'titulo': titulo})

# ------------------------------------------------------------------------------
# 4. GESTÃO DE USUÁRIOS (CRUD SEM ADMIN)
# ------------------------------------------------------------------------------
@login_required
@user_passes_test(is_master)
def master_listar_usuarios(request):
    # Lista usuários que têm perfil vinculado a despachante
    usuarios = User.objects.filter(perfilusuario__isnull=False).select_related('perfilusuario__despachante')
    return render(request, 'master/lista_usuarios.html', {'usuarios': usuarios})

@login_required
@user_passes_test(is_master)
def master_criar_usuario(request):
    if request.method == 'POST':
        form = UsuarioMasterForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    # --- LÓGICA DE LOGIN (NOVO) ---
                    # Pega o que foi digitado no campo 'username' e 'email'
                    login_digitado = form.cleaned_data.get('username')
                    email_digitado = form.cleaned_data['email']
                    
                    # Se digitou login, usa ele. Se deixou em branco, usa o e-mail como login.
                    username_final = login_digitado if login_digitado else email_digitado

                    # 1. Cria User (Auth)
                    novo_user = User.objects.create(
                        username=username_final,  # <--- AQUI ESTÁ A MUDANÇA
                        email=email_digitado,
                        first_name=form.cleaned_data['first_name'],
                        last_name=form.cleaned_data['last_name'],
                        password=make_password(form.cleaned_data['password'])
                    )
                    
                    # 2. Cria Perfil (Vínculo) - Mantido igual
                    PerfilUsuario.objects.create(
                        user=novo_user,
                        despachante=form.cleaned_data['despachante'],
                        tipo_usuario=form.cleaned_data['tipo_usuario'],
                        pode_fazer_upload=True
                    )
                    
                messages.success(request, f"Usuário criado com sucesso! Login de acesso: {username_final}")
                return redirect('master_listar_usuarios')
            
            except Exception as e:
                # O erro mais comum aqui será "UNIQUE constraint failed" se o login já existir
                messages.error(request, f"Erro ao criar usuário: O Login ou E-mail já está em uso.")
    else:
        form = UsuarioMasterForm()

    return render(request, 'master/form_usuario.html', {'form': form})


@login_required
@user_passes_test(is_master)
def master_editar_usuario(request, id):
    user_edit = get_object_or_404(User, id=id)
    
    # Tenta pegar o perfil, se não existir (ex: superuser antigo), cria um temporário na memória
    try:
        perfil = user_edit.perfilusuario
    except PerfilUsuario.DoesNotExist:
        perfil = None

    if request.method == 'POST':
        form = UsuarioMasterEditForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    # 1. Atualiza dados básicos do User
                    user_edit.first_name = form.cleaned_data['first_name']
                    user_edit.last_name = form.cleaned_data['last_name']
                    
                    # 2. Se digitou senha nova, atualiza
                    nova_senha = form.cleaned_data['password']
                    if nova_senha:
                        user_edit.password = make_password(nova_senha)
                    
                    user_edit.save()

                    # 3. Atualiza o Perfil (Vínculo e Permissão)
                    # Se o usuário não tinha perfil, cria agora
                    if not perfil:
                        perfil = PerfilUsuario(user=user_edit)
                    
                    perfil.despachante = form.cleaned_data['despachante']
                    perfil.tipo_usuario = form.cleaned_data['tipo_usuario']
                    perfil.save()

                messages.success(request, f"Usuário {user_edit.email} atualizado com sucesso!")
                return redirect('master_listar_usuarios')
            except Exception as e:
                messages.error(request, f"Erro ao atualizar: {e}")
    else:
        # Preenche o formulário com os dados atuais
        initial_data = {
            'first_name': user_edit.first_name,
            'last_name': user_edit.last_name,
            'email': user_edit.email,
            'despachante': perfil.despachante if perfil else None,
            'tipo_usuario': perfil.tipo_usuario if perfil else 'OPERAR',
        }
        form = UsuarioMasterEditForm(initial=initial_data)

    return render(request, 'master/form_usuario.html', {
        'form': form, 
        'titulo': f"Editar Usuário: {user_edit.first_name}"
    })

# ------------------------------------------------------------------------------
# 5 FLUXO DE CAIXA SIMPLIFICADO
# ------------------------------------------------------------------------------

@login_required
def fluxo_caixa(request):
    despachante = request.user.perfilusuario.despachante
    
    # 1. CAPTURA DE PARÂMETROS DE BUSCA
    data_inicio = request.GET.get('data_inicio')
    data_fim = request.GET.get('data_fim')
    cliente_nome = request.GET.get('cliente')
    status_fin = request.GET.get('status_financeiro')

    # 2. QUERY BASE: Apenas processos APROVADOS (operacionalmente finalizados)
    processos = Atendimento.objects.filter(
        despachante=despachante,
        status='APROVADO'
    ).select_related('cliente', 'veiculo').order_by('-data_solicitacao')

    # 3. LÓGICA DE FILTRAGEM DINÂMICA
    # Se não houver nenhum filtro ativo, mostramos o mês atual por padrão (Reset mensal visual)
    if not any([data_inicio, data_fim, cliente_nome, status_fin]):
        hoje = timezone.now().date()
        processos = processos.filter(
            data_solicitacao__month=hoje.month, 
            data_solicitacao__year=hoje.year
        )
    else:
        # Filtro por período
        if data_inicio:
            processos = processos.filter(data_solicitacao__gte=data_inicio)
        if data_fim:
            processos = processos.filter(data_solicitacao__lte=data_fim)
        
        # Filtro por nome do cliente ou placa
        if cliente_nome:
            processos = processos.filter(
                Q(cliente__nome__icontains=cliente_nome) | 
                Q(veiculo__placa__icontains=cliente_nome)
            )
        
        # Filtro por status de pagamento
        if status_fin:
            processos = processos.filter(status_financeiro=status_fin)

    # 4. AGREGANDO VALORES (Baseado na lista já filtrada)
    dados_financeiros = processos.aggregate(
        total_taxas=Sum('valor_taxas_detran'),
        total_honorarios=Sum('valor_honorarios'),
        total_impostos=Sum('custo_impostos'),
        total_bancario=Sum('custo_taxa_bancaria')
    )

    # 5. MONTAGEM DO RESUMO PARA OS CARDS
    resumo = {
        'total_pendentes': processos.filter(status_financeiro='ABERTO').count(),
        'valor_taxas': dados_financeiros['total_taxas'] or 0,
        'valor_honorarios_bruto': dados_financeiros['total_honorarios'] or 0,
        'valor_impostos': dados_financeiros['total_impostos'] or 0,
        'valor_bancario': dados_financeiros['total_bancario'] or 0,
    }
    
    # Cálculos de Performance do Período Filtrado
    resumo['faturamento_total'] = resumo['valor_taxas'] + resumo['valor_honorarios_bruto']
    resumo['total_custos_operacionais'] = resumo['valor_impostos'] + resumo['valor_bancario']
    resumo['lucro_liquido_total'] = resumo['valor_honorarios_bruto'] - resumo['total_custos_operacionais']

    return render(request, 'cadastro/fluxo_caixa.html', {
        'processos': processos,
        'resumo': resumo,
        'filtros': request.GET # Enviamos de volta para manter os campos do form preenchidos
    })

@login_required
def dar_baixa_pagamento(request, id):
    """View rápida para confirmar recebimento"""
    processo = get_object_or_404(Atendimento, id=id, despachante=request.user.perfilusuario.despachante)
    
    processo.status_financeiro = 'PAGO'
    processo.data_pagamento = timezone.now().date()
    processo.save()
    
    messages.success(request, f"Recebimento de R$ {processo.valor_total_cliente} confirmado!")
    return redirect('fluxo_caixa')

@login_required
def dashboard_financeiro(request):
    despachante = request.user.perfilusuario.despachante
    
    # Dashboard reflete apenas processos finalizados tecnicamente
    processos_fin = Atendimento.objects.filter(
        despachante=despachante, 
        status='APROVADO'
    ).exclude(status='CANCELADO')

    # 1. Totais Gerais
    agregados = processos_fin.aggregate(
        total_taxas=Sum('valor_taxas_detran'),
        total_honorarios=Sum('valor_honorarios'),
        total_impostos=Sum('custo_impostos'),
        total_bancario=Sum('custo_taxa_bancaria')
    )

    h_bruto = agregados['total_honorarios'] or 0
    impostos = agregados['total_impostos'] or 0
    bancario = agregados['total_bancario'] or 0
    lucro_liquido = h_bruto - (impostos + bancario)

    # 2. Dados Gráfico Pizza (Composição do Honorário)
    pie_data = [float(lucro_liquido), float(impostos), float(bancario)]

    # 3. Evolução Mensal (Baseado na data de solicitação dos processos finalizados)
    hoje = timezone.now()
    meses_nomes = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
    
    grafico_evolucao = processos_fin.filter(data_solicitacao__year=hoje.year)\
        .annotate(mes=ExtractMonth('data_solicitacao'))\
        .values('mes')\
        .annotate(total=Sum('valor_honorarios'))\
        .order_by('mes')

    labels_meses = [meses_nomes[item['mes']-1] for item in grafico_evolucao]
    valores_meses = [float(item['total']) for item in grafico_evolucao]

    context = {
        'resumo': {
            'bruto': float((agregados['total_taxas'] or 0) + h_bruto),
            'detran': float(agregados['total_taxas'] or 0),
            'lucro': float(lucro_liquido),
            'pendente': processos_fin.filter(status_financeiro='ABERTO').count()
        },
        'pie_data': json.dumps(pie_data),
        'labels_meses': json.dumps(labels_meses),
        'valores_meses': json.dumps(valores_meses),
    }

    return render(request, 'cadastro/dashboard_financeiro.html', context)

@login_required
def relatorio_inadimplencia(request):
    despachante = request.user.perfilusuario.despachante
    hoje = timezone.now().date()
    
    devedores_qs = Atendimento.objects.filter(
        despachante=despachante,
        status='APROVADO',
        status_financeiro='ABERTO'
    ).select_related('cliente', 'veiculo').order_by('data_solicitacao')

    # Totais
    agregados = devedores_qs.aggregate(
        total_taxas=Sum('valor_taxas_detran'),
        total_honorarios=Sum('valor_honorarios')
    )
    total_taxas = agregados['total_taxas'] or 0
    total_honorarios = agregados['total_honorarios'] or 0

    # Lista final que vai para o template
    lista_devedores = []

    for item in devedores_qs:
        # Cálculos
        dias_atraso = (hoje - item.data_solicitacao).days
        valor_total_calc = (item.valor_taxas_detran or 0) + (item.valor_honorarios or 0)

        # WhatsApp
        tel_bruto = item.cliente.telefone or ""
        telefone_limpo = "".join([c for c in tel_bruto if c.isdigit()])
        
        primeiro_nome = item.cliente.nome.split()[0] if item.cliente.nome else "Cliente"
        placa = item.veiculo.placa if item.veiculo else "S/P"
        
        texto_whatsapp = (
            f"Olá {primeiro_nome}, identificamos uma pendência referente ao serviço de "
            f"{item.servico} (Placa: {placa}).\n"
            f"Valor em aberto: R$ {valor_total_calc:.2f}.\n"
            "Podemos agendar o pagamento?"
        )

        # Criamos um dicionário para não conflitar com propriedades do Model
        lista_devedores.append({
            'id': item.id,
            'dias_atraso': dias_atraso,
            'cliente': item.cliente,
            'servico': item.servico,
            'veiculo': item.veiculo,
            'valor_taxas_detran': item.valor_taxas_detran,
            'valor_honorarios': item.valor_honorarios,
            'valor_total_cliente': valor_total_calc, # Nome chave que o template já usa
            'telefone_limpo': telefone_limpo,
            'texto_whatsapp': texto_whatsapp
        })

    context = {
        'devedores': lista_devedores,
        'total_taxas': total_taxas,
        'total_honorarios': total_honorarios,
        'total_geral': total_taxas + total_honorarios,
        'quantidade': len(lista_devedores)
    }
    
    return render(request, 'cadastro/relatorio_inadimplencia.html', context)

@login_required
def configuracoes_despachante(request):
    despachante = request.user.perfilusuario.despachante
    
    if request.method == 'POST':
        # Pegando os valores do formulário
        aliquota_imp = request.POST.get('aliquota_imposto').replace(',', '.')
        taxa_ban = request.POST.get('taxa_bancaria_padrao').replace(',', '.')
        
        try:
            despachante.aliquota_imposto = aliquota_imp
            despachante.taxa_bancaria_padrao = taxa_ban
            despachante.save()
            messages.success(request, "Configurações financeiras atualizadas com sucesso!")
        except Exception as e:
            messages.error(request, f"Erro ao salvar: {e}")
        
        return redirect('configuracoes_despachante')

    return render(request, 'cadastro/configuracoes_despachante.html', {
        'despachante': despachante
    })

@login_required
def emitir_recibo(request, id):
    # Garante que só o despachante dono do dado pode emitir
    atendimento = get_object_or_404(Atendimento, id=id, despachante=request.user.perfilusuario.despachante)
    
    # Cálculos seguros
    taxas = atendimento.valor_taxas_detran or 0
    honorarios = atendimento.valor_honorarios or 0
    total = taxas + honorarios
    
    context = {
        'atendimento': atendimento,
        'taxas': taxas,
        'honorarios': honorarios,
        'total': total,
        'data_atual': timezone.now().date()
    }
    
    return render(request, 'cadastro/recibo_impressao.html', context)