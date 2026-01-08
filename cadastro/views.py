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

from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login
from django.contrib.sessions.models import Session
from .models import PerfilUsuario  # Importe seu modelo de perfil criado
from .asaas import gerar_boleto_asaas
from django.contrib.auth.models import User
from .forms import UsuarioMasterEditForm 

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
    
    # --- 1. CAPTURA OS FILTROS (DATA E BUSCA) ---
    data_filtro = request.GET.get('data_filtro')
    termo_busca = request.GET.get('busca') # <--- Novo: Captura o texto digitado

    # Fila de Processos Base
    fila_processos = Atendimento.objects.filter(
        despachante=despachante
    ).exclude(
        status__in=['APROVADO', 'CANCELADO']
    ).order_by('data_solicitacao')
    
    # --- 2. APLICA O FILTRO DE DATA (Se existir) ---
    if data_filtro:
        fila_processos = fila_processos.filter(data_solicitacao=data_filtro)

    # --- 3. APLICA A BUSCA DINÂMICA (Se existir texto) ---
    # Isso procura em Nome, Placa, Protocolo ou Serviço ao mesmo tempo
    if termo_busca:
        fila_processos = fila_processos.filter(
            Q(cliente__nome__icontains=termo_busca) |
            Q(veiculo__placa__icontains=termo_busca) |
            Q(numero_atendimento__icontains=termo_busca) |
            Q(servico__icontains=termo_busca)
        )
    
    # Lógica de Alertas (Mantida EXATAMENTE igual a sua)
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

    # Estatísticas (Mantidas iguais)
    total_abertos = Atendimento.objects.filter(despachante=despachante).exclude(status__in=['APROVADO', 'CANCELADO']).count()
    total_mes = Atendimento.objects.filter(
        despachante=despachante, 
        data_solicitacao__month=hoje.month
    ).count()

    context = {
        'fila_processos': fila_processos,
        'total_abertos': total_abertos,
        'total_mes': total_mes,
        'perfil': perfil,
        'data_filtro': data_filtro,
        'termo_busca': termo_busca, # <--- Devolvemos para o template para não sumir do input
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
        form = AtendimentoForm(request.user, request.POST)
        if form.is_valid():
            atendimento = form.save(commit=False)
            atendimento.despachante = perfil.despachante
            atendimento.save()
            return redirect('dashboard')
    else:
        form = AtendimentoForm(request.user)

    return render(request, 'form_generico.html', {
        'form': form,
        'titulo': 'Novo Processo DETRAN'
    })


from django.urls import reverse  # <--- Adicione esse import no topo do arquivo

# ... (seu código) ...

@login_required
def editar_atendimento(request, id):
    perfil = request.user.perfilusuario
    
    # Busca o atendimento
    atendimento = get_object_or_404(Atendimento, id=id, despachante=perfil.despachante)
    
    if request.method == 'POST':
        form = AtendimentoForm(request.user, request.POST, instance=atendimento)
        if form.is_valid():
            form.save()
            return redirect('dashboard')
    else:
        form = AtendimentoForm(request.user, instance=atendimento)
        
    return render(request, 'form_generico.html', {
        'form': form, 
        'titulo': f'Editar Processo #{atendimento.numero_atendimento or "S/N"}',
        
        # --- AQUI ESTÁ A CORREÇÃO ---
        # Enviamos para o template exatamente qual URL deve ser chamada para excluir
        'url_excluir': reverse('excluir_atendimento', args=[atendimento.id]),
        'texto_modal': f"Tem certeza que deseja excluir o processo do veículo {atendimento.veiculo.placa}?",
        'url_voltar': reverse('dashboard') # Botão voltar vai pro dashboard
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
    Agora suporta a escolha de um Responsável Técnico.
    """
    perfil = getattr(request.user, 'perfilusuario', None)
    if not perfil:
        return redirect('logout')

    # --- CARREGAMENTOS (GET) ---
    servicos_db = TipoServico.objects.filter(despachante=perfil.despachante, ativo=True)
    
    # --- NOVO: Busca a equipe do despachante para preencher o Select de responsáveis ---
    equipe = PerfilUsuario.objects.filter(
        despachante=perfil.despachante
    ).select_related('user')

    if request.method == 'POST':
        try:
            with transaction.atomic():
                despachante = perfil.despachante

                # --- NOVO: Define quem é o responsável ---
                responsavel_id = request.POST.get('responsavel_id')
                responsavel_obj = request.user # Padrão: Usuário logado
                
                if responsavel_id:
                    # Tenta buscar o usuário selecionado
                    try:
                        responsavel_obj = User.objects.get(id=responsavel_id)
                    except User.DoesNotExist:
                        pass # Se der erro, mantém o usuário logado como fallback

                # 1. VERIFICA SE UM CLIENTE FOI SELECIONADO
                cliente_id = request.POST.get('cliente_id')
                if not cliente_id:
                    print("❌ Erro: Nenhum cliente selecionado.")
                    return redirect('cadastro_rapido')
                
                cliente = get_object_or_404(Cliente, id=cliente_id, despachante=despachante)

                # 2. CAPTURA AS LISTAS DE DADOS
                placas = request.POST.getlist('veiculo_placa[]')
                renavams = request.POST.getlist('veiculo_renavam[]')
                modelos = request.POST.getlist('veiculo_modelo[]')
                cores = request.POST.getlist('veiculo_cor[]')
                anos = request.POST.getlist('veiculo_ano[]')
                marcas = request.POST.getlist('veiculo_marca[]')
                chassis = request.POST.getlist('veiculo_chassi[]')
                tipos = request.POST.getlist('veiculo_tipo[]')
                
                servicos = request.POST.getlist('servico[]')
                atendimentos = request.POST.getlist('numero_atendimento[]')
                obs_geral = request.POST.get('observacoes', '')
                
                prazo_input = request.POST.get('prazo_entrega')

                # 3. LOOP PARA SALVAR VEÍCULOS E CRIAR PROCESSOS
                for i in range(len(placas)):
                    # Limpeza básica da placa
                    placa_limpa = placas[i].replace('-', '').replace(' ', '').upper()
                    if not placa_limpa: continue
                    if len(placa_limpa) > 7: placa_limpa = placa_limpa[:7]

                    # Tratamento de ano
                    af = anos[i] if (i < len(anos) and anos[i] and anos[i].isdigit()) else 2000
                    
                    # Cria ou Atualiza o Veículo
                    veiculo, _ = Veiculo.objects.get_or_create(
                        placa=placa_limpa,
                        despachante=despachante,
                        defaults={
                            'cliente': cliente,
                            'renavam': renavams[i] if i < len(renavams) else '',
                            'modelo': modelos[i] if i < len(modelos) else '',
                            'cor': cores[i] if i < len(cores) else '',
                            'ano_fabricacao': af,
                            'ano_modelo': af, 
                            'marca': marcas[i] if i < len(marcas) else '',
                            'chassi': chassis[i] if i < len(chassis) else '',
                            'tipo': tipos[i] if i < len(tipos) else 'CARRO'
                        }
                    )

                    # Cria o Processo (Atendimento)
                    servico_atual = servicos[i] if i < len(servicos) else 'Desconhecido'
                    num_atend_atual = atendimentos[i] if i < len(atendimentos) else ''

                    Atendimento.objects.create(
                        despachante=despachante,
                        cliente=cliente,
                        veiculo=veiculo,
                        servico=servico_atual,
                        
                        # --- GRAVA O RESPONSÁVEL AQUI ---
                        responsavel=responsavel_obj,
                        
                        numero_atendimento=num_atend_atual,
                        observacoes_internas=obs_geral,
                        data_entrega=prazo_input if prazo_input else None,
                        status='SOLICITADO'
                    )

            return redirect('dashboard')

        except Exception as e:
            print(f"❌ Erro Crítico no Cadastro Rápido: {e}")
            pass

    # Renderiza o template passando serviços e a equipe
    return render(request, 'processos/cadastro_rapido.html', {
        'servicos_db': servicos_db,
        'equipe': equipe  # Variável nova disponível no template
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
        v_base = request.POST.get('valor_base').replace(',', '.')
        v_hon = request.POST.get('honorarios').replace(',', '.')
        
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
        # 1. Dados Básicos
        cliente_id = request.POST.get('cliente_id')
        nome_avulso = request.POST.get('cliente_nome_avulso')
        observacoes = request.POST.get('observacoes')
        desconto = request.POST.get('desconto') or 0
        valor_total_hidden = request.POST.get('valor_total_hidden') or 0

        # 2. Cria o Objeto Orçamento
        orcamento = Orcamento.objects.create(
            despachante=perfil.despachante,
            observacoes=observacoes,
            desconto=desconto,
            valor_total=valor_total_hidden,
            status='PENDENTE'
        )

        # 3. Vincula Cliente (Ou nome avulso)
        if cliente_id:
            orcamento.cliente = Cliente.objects.filter(id=cliente_id).first()
        elif nome_avulso:
            orcamento.nome_cliente_avulso = nome_avulso
        
        orcamento.save()

        # 4. Salva os Itens
        ids_servicos = request.POST.getlist('servicos[]') 
        precos_servicos = request.POST.getlist('precos[]')
        
        # Precisamos pegar o NOME do serviço de novo para garantir
        for servico_id, preco in zip(ids_servicos, precos_servicos):
            servico_obj = TipoServico.objects.filter(id=servico_id).first()
            nome_servico = servico_obj.nome if servico_obj else "Serviço Removido"
            
            ItemOrcamento.objects.create(
                orcamento=orcamento,
                servico_nome=nome_servico,
                valor=preco
            )

        messages.success(request, f"Orçamento #{orcamento.id} salvo com sucesso!")
        
        # Redireciona para a tela de visualização/impressão desse orçamento específico
        return redirect('detalhe_orcamento', id=orcamento.id)

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
    # 1. Busca o orçamento
    orcamento = get_object_or_404(Orcamento, id=id, despachante=request.user.perfilusuario.despachante)
    
    # 2. Verifica se já não foi aprovado antes
    if orcamento.status != 'PENDENTE':
        messages.warning(request, "Este orçamento já foi finalizado anteriormente.")
        return redirect('detalhe_orcamento', id=id)
    
    # 3. Atualiza o status
    orcamento.status = 'APROVADO'
    orcamento.save()
    
    messages.success(request, "Orçamento Aprovado! Selecione o veículo para concluir o cadastro.")
    
    # 4. Redireciona para o Cadastro Rápido
    # Se tiver cliente cadastrado, passa o ID na URL para o JS capturar
    if orcamento.cliente:
        return redirect(f'/novo-processo-rapido/?cliente_id={orcamento.cliente.id}&orcamento_origem={orcamento.id}')
    
    # Se for cliente avulso, vai para a tela em branco (pois não tem cadastro de veículos ainda)
    return redirect('cadastro_rapido')

from django.db.models import Q # Importe o Q no topo do arquivo se não tiver

@login_required
def listar_orcamentos(request):
    # 1. Pega os parâmetros
    termo = request.GET.get('termo', '').strip()
    status_filtro = request.GET.get('status')
    
    # 2. Segurança
    perfil = getattr(request.user, 'perfilusuario', None)
    if not perfil:
        return redirect('logout')

    # 3. Query Base
    # CORREÇÃO: Removi 'veiculo' do select_related pois o campo não existe no Orcamento
    orcamentos = Orcamento.objects.filter(
        despachante=perfil.despachante
    ).select_related('cliente').order_by('-data_criacao')
    
    # 4. Filtro de Busca
    if termo:
        # Busca Textual (Nome, CPF, Nome Avulso)
        # CORREÇÃO: Removi a busca por placa/modelo pois o Orcamento não tem link direto com veículo
        filtros = (
            Q(cliente__nome__icontains=termo) |              # Nome do Cliente Cadastrado
            Q(cliente__cpf_cnpj__icontains=termo) |          # CPF/CNPJ
            Q(nome_cliente_avulso__icontains=termo)          # Nome Avulso
        )

        # Se for número, inclui busca pelo ID do orçamento
        if termo.isdigit():
            filtros |= Q(id=termo)

        # Aplica o filtro
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
    try:
        perfil = request.user.perfilusuario
    except PerfilUsuario.DoesNotExist:
        return render(request, 'erro_perfil.html')

    # 1. Descobrir Mês e Ano (Do filtro ou do Atual)
    hoje = timezone.now().date()
    mes_filtro = request.GET.get('mes', hoje.month)
    ano_filtro = request.GET.get('ano', hoje.year)
    
    try:
        mes_filtro = int(mes_filtro)
        ano_filtro = int(ano_filtro)
    except ValueError:
        mes_filtro = hoje.month
        ano_filtro = hoje.year

    # 2. Filtrar os atendimentos do mês selecionado
    processos = Atendimento.objects.filter(
        despachante=perfil.despachante,
        data_solicitacao__month=mes_filtro,
        data_solicitacao__year=ano_filtro
    ).order_by('data_solicitacao')

    # 3. Pequeno resumo estatístico para o cabeçalho do relatório
    total_qtd = processos.count()
    
    # Agrupamento por status (Ex: Quantos aprovados, quantos cancelados)
    resumo_status = processos.values('status').annotate(total=Count('status'))

    # Lista de meses para o dropdown do filtro
    meses_ano = [
        (1, 'Janeiro'), (2, 'Fevereiro'), (3, 'Março'), (4, 'Abril'),
        (5, 'Maio'), (6, 'Junho'), (7, 'Julho'), (8, 'Agosto'),
        (9, 'Setembro'), (10, 'Outubro'), (11, 'Novembro'), (12, 'Dezembro')
    ]

    context = {
        'processos': processos,
        'total_qtd': total_qtd,
        'resumo_status': resumo_status,
        'mes_atual': mes_filtro,
        'ano_atual': ano_filtro,
        'meses_lista': meses_ano,
        'anos_lista': range(hoje.year - 2, hoje.year + 2), # Ex: 2023 a 2027
    }

    return render(request, 'relatorios/relatorio_mensal.html', context)


@login_required
def relatorio_servicos(request):
    # --- 1. Filtros (Mantido igual) ---
    data_inicio = request.GET.get('data_inicio')
    data_fim = request.GET.get('data_fim')
    cliente_placa = request.GET.get('cliente_placa')
    status_filtro = request.GET.get('status')

    atendimentos = Atendimento.objects.filter(despachante=request.user.perfilusuario.despachante)

    if data_inicio:
        atendimentos = atendimentos.filter(data_solicitacao__gte=data_inicio)
    if data_fim:
        atendimentos = atendimentos.filter(data_solicitacao__lte=data_fim)
    if status_filtro:
        atendimentos = atendimentos.filter(status=status_filtro)
    if cliente_placa:
        atendimentos = atendimentos.filter(
            Q(cliente__nome__icontains=cliente_placa) |
            Q(veiculo__placa__icontains=cliente_placa)
        )

    # Ordena por cliente primeiro (para agrupar visualmente) e depois por data
    atendimentos = atendimentos.order_by('cliente__nome', '-data_solicitacao')

    # --- 2. Lógica de Agrupamento e Cálculo ---
    relatorio_agrupado = {} # Dicionário principal
    total_geral_honorarios = 0
    total_geral_valor = 0

    # Carrega tabela de preços para consulta rápida
    todos_servicos = {s.nome.upper(): s for s in TipoServico.objects.filter(despachante=request.user.perfilusuario.despachante)}

    for item in atendimentos:
        # A. Lógica de Preço (Quebra string 'Serviço A + Serviço B')
        nome_completo = item.servico
        nomes_individuais = nome_completo.split(' + ')
        
        honorario_item = 0
        valor_total_item = 0
        
        for nome in nomes_individuais:
            nome_limpo = nome.strip().upper()
            servico_obj = todos_servicos.get(nome_limpo)
            if servico_obj:
                honorario_item += servico_obj.honorarios
                valor_total_item += servico_obj.valor_total

        # B. Lógica de Agrupamento por Cliente
        cliente_id = item.cliente.id
        
        # Se o cliente ainda não está no dicionário, cria a estrutura dele
        if cliente_id not in relatorio_agrupado:
            relatorio_agrupado[cliente_id] = {
                'dados_cliente': item.cliente, # Objeto cliente completo
                'itens': [],
                'subtotal_honorarios': 0,
                'subtotal_valor': 0
            }

        # Adiciona o item na lista deste cliente
        relatorio_agrupado[cliente_id]['itens'].append({
            'data': item.data_solicitacao,
            'placa': item.veiculo.placa,
            'modelo': item.veiculo.modelo, # Útil para exibir no relatório
            'servico_nome': item.servico,
            'honorario': honorario_item,
            'valor_total': valor_total_item,
            'status': item.get_status_display()
        })

        # Atualiza Subtotais do Cliente
        relatorio_agrupado[cliente_id]['subtotal_honorarios'] += honorario_item
        relatorio_agrupado[cliente_id]['subtotal_valor'] += valor_total_item

        # Atualiza Totais Gerais do Relatório
        total_geral_honorarios += honorario_item
        total_geral_valor += valor_total_item

    context = {
        'relatorio_agrupado': relatorio_agrupado, # Passamos o dicionário agrupado
        'total_geral_honorarios': total_geral_honorarios,
        'total_geral_valor': total_geral_valor,
        'filtros': request.GET
    }

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