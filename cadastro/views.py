from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils import timezone
from django.db.models import Q, Sum, Count
from django.db import transaction
from django.http import JsonResponse, FileResponse
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password
from django.urls import reverse
from django.db.models.functions import ExtractMonth
import re
import json
import base64
from datetime import timedelta

# Importação dos Modelos e Forms
from .models import Atendimento, Cliente, Veiculo, TipoServico, PerfilUsuario, Despachante, Orcamento, ItemOrcamento
from .forms import AtendimentoForm, ClienteForm, VeiculoForm, DespachanteForm, UsuarioMasterForm, UsuarioMasterEditForm, CompressaoPDFForm
from .asaas import gerar_boleto_asaas
from .utils import comprimir_pdf_memoria

# ==============================================================================
# 1. LOGIN E AUTENTICAÇÃO
# ==============================================================================
def minha_view_de_login(request):
    contexto = {'erro_login': False}

    if request.method == 'POST':
        login_input = request.POST.get('username') 
        password_form = request.POST.get('password')
        
        username_para_autenticar = login_input

        # Verifica se é E-mail
        if '@' in login_input:
            try:
                user_obj = User.objects.get(email=login_input)
                username_para_autenticar = user_obj.username
            except User.DoesNotExist:
                pass

        user = authenticate(request, username=username_para_autenticar, password=password_form)

        if user is not None:
            # ⛔ BLOQUEIO FINANCEIRO
            try:
                perfil_check = user.perfilusuario
                if perfil_check.data_expiracao and perfil_check.data_expiracao < timezone.now().date():
                    data_venc = perfil_check.data_expiracao.strftime('%d/%m/%Y')
                    messages.error(request, f"🔒 Acesso Bloqueado: Sua assinatura venceu em {data_venc}.")
                    contexto['erro_login'] = True
                    return render(request, 'login.html', context=contexto)
            except AttributeError:
                pass

            login(request, user)

            if not request.session.session_key:
                request.session.create()

            nova_chave = request.session.session_key

            # Single Session
            perfil, created = PerfilUsuario.objects.get_or_create(user=user)
            chave_antiga = perfil.ultimo_session_key

            if chave_antiga and chave_antiga != nova_chave:
                try:
                    Session.objects.get(session_key=chave_antiga).delete()
                except Session.DoesNotExist:
                    pass

            perfil.ultimo_session_key = nova_chave
            perfil.save()

            return redirect('dashboard')
        
        else:
            contexto['erro_login'] = True
            messages.error(request, "Usuário ou senha incorretos.")

    return render(request, 'login.html', context=contexto)

@login_required
def pagar_mensalidade(request):
    try:
        despachante = request.user.perfilusuario.despachante
    except AttributeError:
        messages.error(request, "Usuário sem perfil vinculado.")
        return redirect('dashboard')

    resultado = gerar_boleto_asaas(despachante)

    if resultado['sucesso']:
        return redirect(resultado['link_fatura'])
    else:
        messages.error(request, f"Erro ao gerar fatura: {resultado.get('erro')}")
        return redirect('dashboard')

# ==============================================================================
# DASHBOARD
# ==============================================================================
@login_required
def dashboard(request):
    try:
        perfil = request.user.perfilusuario
    except PerfilUsuario.DoesNotExist:
        return render(request, 'erro_perfil.html') 
    
    despachante = perfil.despachante
    
    data_filtro = request.GET.get('data_filtro')
    termo_busca = request.GET.get('busca')

    # ==============================================================================
    # 1. DEFINIÇÃO DO QUE NÃO DEVE APARECER (Processos Finalizados)
    # ==============================================================================
    # Adicione aqui todos os status que significam "Fim de papo"
    status_finalizados = ['APROVADO', 'CANCELADO', 'CONCLUIDO', 'ENTREGUE']

    # ==============================================================================
    # 2. ESTATÍSTICAS (Sem Cache para atualização instantânea)
    # ==============================================================================
    hoje = timezone.now().date()
    
    # Conta apenas o que NÃO está finalizado
    total_abertos = Atendimento.objects.filter(
        despachante=despachante
    ).exclude(
        status__in=status_finalizados
    ).count()

    # Conta tudo que foi solicitado neste mês (independente do status)
    total_mes = Atendimento.objects.filter(
        despachante=despachante, 
        data_solicitacao__month=hoje.month,
        data_solicitacao__year=hoje.year
    ).count()

    # ==============================================================================
    # 3. LISTA DA FILA DE TRABALHO
    # ==============================================================================
    fila_processos = Atendimento.objects.select_related(
        'cliente', 'veiculo', 'responsavel'
    ).filter(
        despachante=despachante
    ).exclude(
        status__in=status_finalizados  # <--- AQUI GARANTE QUE ZERA/SOME
    ).order_by('data_solicitacao')
    
    # Filtro por Data (se selecionado no input)
    if data_filtro:
        fila_processos = fila_processos.filter(data_solicitacao=data_filtro)

    # Filtro por Busca (se digitado)
    if termo_busca:
        fila_processos = fila_processos.filter(
            Q(cliente__nome__icontains=termo_busca) |
            Q(veiculo__placa__icontains=termo_busca) |
            Q(numero_atendimento__icontains=termo_busca) |
            Q(servico__icontains=termo_busca)
        )
    
    # ==============================================================================
    # 4. LÓGICA DE CORES E PRAZOS
    # ==============================================================================
    for processo in fila_processos:
        if processo.data_entrega:
            dias_restantes = (processo.data_entrega - hoje).days
            processo.dias_na_fila = dias_restantes
            
            if dias_restantes < 0:
                processo.alerta_cor = 'danger'   # Atrasado
            elif dias_restantes <= 2:
                processo.alerta_cor = 'warning'  # Perto do prazo
            else:
                processo.alerta_cor = 'success'  # No prazo
        else:
            # Se não tem data de entrega definida, baseia na data de solicitação
            dias_corridos = (hoje - processo.data_solicitacao).days
            if dias_corridos >= 30:
                processo.alerta_cor = 'danger'
            elif dias_corridos >= 15:
                processo.alerta_cor = 'warning'
            else:
                processo.alerta_cor = 'success'

    context = {
        'fila_processos': fila_processos,
        'total_abertos': total_abertos, 
        'total_mes': total_mes,
        'perfil': perfil,
        'data_filtro': data_filtro,
        'termo_busca': termo_busca,
    }
    
    return render(request, 'dashboard.html', context)

# ==============================================================================
# GESTÃO DE ATENDIMENTOS (CRUD) - CÓDIGO CORRIGIDO
# ==============================================================================

@login_required
def novo_atendimento(request):
    perfil = getattr(request.user, 'perfilusuario', None)
    if not perfil:
        return redirect('dashboard')
    
    despachante = perfil.despachante

    if request.method == 'POST':
        form = AtendimentoForm(request.user, request.POST)
        if form.is_valid():
            atendimento = form.save(commit=False)
            atendimento.despachante = despachante
            
            # --- CÁLCULO FINANCEIRO (CORRIGIDO) ---
            h_bruto = atendimento.valor_honorarios or 0
            
            # Converte para float para fazer a conta
            aliquota = float(despachante.aliquota_imposto or 0)
            taxa_bancaria_config = float(despachante.taxa_bancaria_padrao or 0)
            
            # AQUI ESTÁ A CORREÇÃO: DIVIDIR POR 100
            # Antes: h_bruto * aliquota (Dava 939.00)
            # Agora: h_bruto * (aliquota / 100) (Vai dar 9.39)
            atendimento.custo_impostos = float(h_bruto) * (aliquota / 100)
            atendimento.custo_taxa_bancaria = float(h_bruto) * (taxa_bancaria_config / 100)
            # --------------------------------------

            if not atendimento.responsavel:
                atendimento.responsavel = request.user
            
            atendimento.save()
            messages.success(request, "Processo criado com sucesso!")
            return redirect('dashboard')
    else:
        form = AtendimentoForm(request.user, initial={'responsavel': request.user})

    return render(request, 'form_generico.html', {
        'form': form,
        'titulo': 'Novo Processo DETRAN'
    })

@login_required
def editar_atendimento(request, id):
    perfil = request.user.perfilusuario
    despachante = perfil.despachante
    
    atendimento = get_object_or_404(Atendimento, id=id, despachante=despachante)
    
    if request.method == 'POST':
        form = AtendimentoForm(request.user, request.POST, instance=atendimento)
        
        if form.is_valid():
            atendimento_obj = form.save(commit=False)
            
            # --- CÁLCULO FINANCEIRO (CORRIGIDO) ---
            h_bruto = atendimento_obj.valor_honorarios or 0
            
            aliquota = float(despachante.aliquota_imposto or 0)
            taxa_bancaria_config = float(despachante.taxa_bancaria_padrao or 0)
            
            # CORREÇÃO AQUI TAMBÉM:
            atendimento_obj.custo_impostos = float(h_bruto) * (aliquota / 100)
            atendimento_obj.custo_taxa_bancaria = float(h_bruto) * (taxa_bancaria_config / 100)
            # --------------------------------------
            
            atendimento_obj.save()
            
            messages.success(request, f"Processo {atendimento_obj.numero_atendimento or id} atualizado!")
            return redirect('dashboard')
    else:
        form = AtendimentoForm(request.user, instance=atendimento)
        
    info_veiculo = f"do veículo {atendimento.veiculo.placa}" if atendimento.veiculo else "(Sem veículo vinculado)"

    return render(request, 'form_generico.html', {
        'form': form, 
        'titulo': f'Editar Processo #{atendimento.numero_atendimento or "S/N"}',
        'url_excluir': reverse('excluir_atendimento', args=[atendimento.id]),
        'texto_modal': f"Tem certeza que deseja excluir o processo {info_veiculo}?",
        'url_voltar': reverse('dashboard')
    })

@login_required
def excluir_atendimento(request, id):
    try:
        perfil = request.user.perfilusuario
    except:
        return redirect('dashboard')

    atendimento = get_object_or_404(Atendimento, id=id, despachante=perfil.despachante)

    if perfil.tipo_usuario != 'ADMIN' and not request.user.is_superuser:
        messages.error(request, "⛔ Permissão Negada: Apenas Administradores podem excluir processos.")
        return redirect('dashboard')

    if request.method == 'POST':
        atendimento.delete()
        messages.success(request, "Processo removido com sucesso.")
        return redirect('dashboard')

    return redirect('dashboard')

# ==============================================================================
# CADASTRO RÁPIDO (LOTE)
# ==============================================================================
@login_required
def cadastro_rapido(request):
    perfil = getattr(request.user, 'perfilusuario', None)
    if not perfil:
        return redirect('logout')

    despachante = perfil.despachante
    servicos_db = TipoServico.objects.filter(despachante=despachante, ativo=True)
    equipe = PerfilUsuario.objects.filter(despachante=despachante).select_related('user')

    if request.method == 'POST':
        try:
            with transaction.atomic():
                # --- CORREÇÃO DO RESPONSÁVEL ---
                # Tenta pegar 'responsavel' OU 'responsavel_id'
                responsavel_id = request.POST.get('responsavel') or request.POST.get('responsavel_id')
                
                responsavel_obj = request.user 
                if responsavel_id:
                    try:
                        responsavel_obj = User.objects.get(id=responsavel_id)
                    except User.DoesNotExist:
                        pass

                # Cliente
                cliente_id = request.POST.get('cliente_id')
                if not cliente_id:
                    messages.error(request, "Nenhum cliente selecionado.")
                    return redirect('cadastro_rapido')
                
                cliente = get_object_or_404(Cliente, id=cliente_id, despachante=despachante)

                # Listas
                placas = request.POST.getlist('veiculo_placa[]')
                modelos = request.POST.getlist('veiculo_modelo[]')
                servicos_str_lista = request.POST.getlist('servico[]') 
                atendimentos = request.POST.getlist('numero_atendimento[]')
                
                obs_geral = request.POST.get('observacoes', '')
                prazo_input = request.POST.get('prazo_entrega')

                for i in range(len(placas)):
                    placa_limpa = placas[i].replace('-', '').replace(' ', '').upper()
                    if not placa_limpa: continue

                    veiculo, _ = Veiculo.objects.get_or_create(
                        placa=placa_limpa,
                        despachante=despachante,
                        defaults={'cliente': cliente, 'modelo': modelos[i].upper()}
                    )

                    # Financeiro
                    nomes_selecionados = [s.strip() for s in servicos_str_lista[i].split('+')]
                    total_taxas = 0
                    total_honorarios = 0

                    for nome_s in nomes_selecionados:
                        s_base = servicos_db.filter(nome__iexact=nome_s).first()
                        if s_base:
                            total_taxas += s_base.valor_base
                            total_honorarios += s_base.honorarios

                    custo_imp = total_honorarios * (despachante.aliquota_imposto / 100)
                    custo_ban = total_honorarios * (despachante.taxa_bancaria_padrao / 100)

                    Atendimento.objects.create(
                        despachante=despachante,
                        cliente=cliente,
                        veiculo=veiculo,
                        servico=servicos_str_lista[i],
                        responsavel=responsavel_obj,
                        numero_atendimento=atendimentos[i] if i < len(atendimentos) else '',
                        
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

            messages.success(request, f"{len(placas)} processos criados!")
            return redirect('dashboard')

        except Exception as e:
            messages.error(request, f"Erro ao processar lote: {e}")
            return redirect('cadastro_rapido')

    return render(request, 'processos/cadastro_rapido.html', {
        'servicos_db': servicos_db,
        'equipe': equipe
    })

# ==============================================================================
# CLIENTES E VEICULOS
# ==============================================================================
@login_required
def novo_cliente(request):
    perfil = getattr(request.user, 'perfilusuario', None)
    if not perfil:
        return redirect('logout')

    if request.method == 'POST':
        try:
            with transaction.atomic():
                despachante = perfil.despachante
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
                        'filiacao': request.POST.get('filiacao'),
                        'uf_rg': request.POST.get('uf_rg'),
                        'cep': request.POST.get('cep'),
                        'rua': request.POST.get('rua'),
                        'numero': request.POST.get('numero'),
                        'bairro': request.POST.get('bairro'),
                        'cidade': request.POST.get('cidade', 'Goiânia'),
                        'uf': request.POST.get('uf', 'GO'),
                        'complemento': request.POST.get('complemento'),
                    }
                )

                if not created:
                    cliente.nome = request.POST.get('cliente_nome')
                    cliente.telefone = request.POST.get('cliente_telefone')
                    cliente.email = request.POST.get('cliente_email')
                    cliente.filiacao = request.POST.get('filiacao')
                    cliente.uf_rg = request.POST.get('uf_rg')
                    cliente.rg = request.POST.get('rg')
                    cliente.rua = request.POST.get('rua')
                    cliente.save()

                placas = request.POST.getlist('veiculo_placa[]')
                modelos = request.POST.getlist('veiculo_modelo[]')
                renavams = request.POST.getlist('veiculo_renavam[]')
                
                for i in range(len(placas)):
                    placa_limpa = placas[i].replace('-', '').replace(' ', '').upper()
                    if not placa_limpa: continue
                    if len(placa_limpa) > 7: placa_limpa = placa_limpa[:7]

                    Veiculo.objects.get_or_create(
                        placa=placa_limpa,
                        despachante=despachante,
                        defaults={
                            'cliente': cliente, 
                            'modelo': modelos[i] if i < len(modelos) else '',
                            'renavam': renavams[i] if i < len(renavams) else ''
                        }
                    )

            return redirect('dashboard')

        except Exception as e:
            print(f"❌ Erro no Cadastro Cliente: {e}")
            pass

    return render(request, 'clientes/cadastro_cliente.html')

@login_required
def novo_veiculo(request):
    if request.method == 'POST':
        form = VeiculoForm(request.user, request.POST)
        if form.is_valid():
            veiculo = form.save(commit=False)
            veiculo.despachante = request.user.perfilusuario.despachante
            veiculo.save()
            return redirect('dashboard')
    else:
        form = VeiculoForm(request.user)
    
    return render(request, 'form_generico.html', {'form': form, 'titulo': 'Cadastrar Veículo'})

@login_required
def lista_clientes(request):
    perfil = request.user.perfilusuario
    clientes = Cliente.objects.filter(despachante=perfil.despachante).order_by('nome')
    search_term = request.GET.get('q')

    if search_term:
        clientes = clientes.filter(
            Q(nome__icontains=search_term) | 
            Q(cpf_cnpj__icontains=search_term) |
            Q(telefone__icontains=search_term)
        )

    return render(request, 'clientes/lista_clientes.html', {'clientes': clientes})

@login_required
def detalhe_cliente(request, id):
    perfil = request.user.perfilusuario
    cliente = get_object_or_404(Cliente, id=id, despachante=perfil.despachante)
    veiculos = Veiculo.objects.filter(cliente_id=cliente.id, despachante=perfil.despachante).order_by('-id')
    return render(request, 'clientes/detalhe_cliente.html', {'cliente': cliente, 'veiculos': veiculos})

@login_required
def editar_cliente(request, id):
    perfil = request.user.perfilusuario
    cliente = get_object_or_404(Cliente, id=id, despachante=perfil.despachante)
    
    if request.method == 'POST':
        form = ClienteForm(request.POST, instance=cliente)
        if form.is_valid():
            form.save()
            return redirect('lista_clientes')
    else:
        form = ClienteForm(instance=cliente)

    return render(request, 'clientes/editar_cliente.html', {'form': form})

@login_required
def editar_veiculo(request, id):
    perfil = getattr(request.user, 'perfilusuario', None)
    veiculo = get_object_or_404(Veiculo, id=id, despachante=perfil.despachante)

    if request.method == 'POST':
        form = VeiculoForm(request.user, request.POST, instance=veiculo)
        if form.is_valid():
            form.save()
            return redirect('dashboard')
    else:
        form = VeiculoForm(request.user, instance=veiculo)

    return render(request, 'veiculos/editar_veiculo.html', {'form': form})

@login_required
def excluir_cliente(request, id):
    perfil = request.user.perfilusuario
    cliente = get_object_or_404(Cliente, id=id, despachante=perfil.despachante)

    if not request.user.is_superuser and perfil.tipo_usuario != 'ADMIN':
        messages.error(request, "⛔ Apenas Administradores podem excluir clientes.")
        return redirect('lista_clientes')

    if request.method == 'POST':
        try:
            cliente.delete()
            messages.success(request, f"Cliente '{cliente.nome}' excluído com sucesso.")
        except Exception:
            messages.error(request, "Não é possível excluir este cliente pois ele possui registros vinculados.")
        return redirect('lista_clientes')
    
    return redirect('lista_clientes')

@login_required
def excluir_veiculo(request, id):
    perfil = request.user.perfilusuario
    veiculo = get_object_or_404(Veiculo, id=id, despachante=perfil.despachante)

    if not request.user.is_superuser and perfil.tipo_usuario != 'ADMIN':
        messages.error(request, "⛔ Apenas Administradores podem excluir veículos.")
        return redirect('lista_clientes')

    if request.method == 'POST':
        veiculo.delete()
        messages.success(request, "Veículo excluído.")
        return redirect('lista_clientes')

    return redirect('lista_clientes')

# ==============================================================================
# GESTÃO DE SERVIÇOS E APIS
# ==============================================================================
@login_required
def gerenciar_servicos(request):
    perfil = request.user.perfilusuario
    servicos = TipoServico.objects.filter(despachante=perfil.despachante, ativo=True)
    
    if request.method == 'POST':
        nome = request.POST.get('nome')
        raw_base = request.POST.get('valor_base')
        raw_hon = request.POST.get('honorarios')

        v_base = raw_base.replace(',', '.') if raw_base else 0
        v_hon = raw_hon.replace(',', '.') if raw_hon else 0
        
        TipoServico.objects.create(despachante=perfil.despachante, nome=nome, valor_base=v_base, honorarios=v_hon)
        return redirect('gerenciar_servicos')

    return render(request, 'gerenciar_servicos.html', {'servicos': servicos})

@login_required
def editar_servico(request, id):
    if not request.user.is_superuser and not request.user.perfilusuario.tipo_usuario == 'ADMIN':
        messages.error(request, "Você não tem permissão para editar serviços.")
        return redirect('gerenciar_servicos')

    servico = get_object_or_404(TipoServico, id=id)

    if request.method == 'POST':
        try:
            servico.nome = request.POST.get('nome')
            servico.valor_base = request.POST.get('valor_base', '0').replace('.', '').replace(',', '.')
            servico.honorarios = request.POST.get('honorarios', '0').replace('.', '').replace(',', '.')
            servico.save()
            messages.success(request, f"Serviço '{servico.nome}' atualizado!")
            return redirect('gerenciar_servicos')
        except Exception:
            messages.error(request, "Erro ao atualizar valores.")

    return render(request, 'cadastro/editar_servico.html', {'servico': servico})

@login_required
def excluir_servico(request, id):
    perfil = request.user.perfilusuario
    servico = get_object_or_404(TipoServico, id=id, despachante=perfil.despachante)
    
    if perfil.tipo_usuario != 'ADMIN' and not request.user.is_superuser:
        messages.error(request, "⛔ Permissão Negada.")
        return redirect('gerenciar_servicos')

    servico.ativo = False
    servico.save()
    messages.success(request, "Serviço removido.")
    return redirect('gerenciar_servicos')

@login_required
def buscar_clientes(request):
    term = request.GET.get('term', '')
    perfil = getattr(request.user, 'perfilusuario', None)
    if not perfil or not perfil.despachante:
        return JsonResponse({'results': []}, safe=False)

    despachante = perfil.despachante
    filters = Q(despachante=despachante)

    if term:
        term_limpo = re.sub(r'\D', '', term) 
        filters = filters & (
            Q(nome__icontains=term) | 
            Q(cpf_cnpj__icontains=term) | 
            Q(telefone__icontains=term) |
            Q(veiculos__placa__icontains=term)  
        )
        if term_limpo:
             filters |= Q(cpf_cnpj__icontains=term_limpo)

    clientes = Cliente.objects.filter(filters).distinct().order_by('nome')[:20]
    results = [{'id': c.id, 'text': f"{c.nome.upper()} - {c.cpf_cnpj}"} for c in clientes]
    return JsonResponse({'results': results}, safe=False)

@login_required
def api_veiculos_cliente(request, cliente_id):
    despachante = request.user.perfilusuario.despachante
    veiculos = Veiculo.objects.filter(cliente_id=cliente_id, despachante=despachante)
    
    data = [{
        'id': v.id,
        'placa': v.placa,
        'modelo': v.modelo,
        'renavam': v.renavam or '',
        'marca': v.marca or '',
        'cor': v.cor,
        'ano_fab': v.ano_fabricacao,
        'ano_mod': v.ano_modelo,
        'tipo': v.tipo
    } for v in veiculos]
    
    return JsonResponse(data, safe=False)

# ==============================================================================
# ORÇAMENTOS
# ==============================================================================
@login_required
def novo_orcamento(request):
    perfil = request.user.perfilusuario
    servicos_disponiveis = TipoServico.objects.filter(despachante=perfil.despachante, ativo=True)
    
    if request.method == 'POST':
        try:
            with transaction.atomic():
                def limpar_valor(valor):
                    if not valor: return 0.0
                    v = str(valor).strip()
                    if ',' in v:
                        v = v.replace('.', '').replace(',', '.')
                    return float(v)

                desconto = limpar_valor(request.POST.get('desconto'))
                valor_total = limpar_valor(request.POST.get('valor_total_hidden'))
                
                cliente_id = request.POST.get('cliente_id')
                nome_avulso = request.POST.get('cliente_nome_avulso')
                observacoes = request.POST.get('observacoes')
                veiculo_id = request.POST.get('veiculo_id')
                
                veiculo_obj = None
                if veiculo_id:
                    veiculo_obj = Veiculo.objects.filter(id=veiculo_id).first()

                orcamento = Orcamento.objects.create(
                    despachante=perfil.despachante,
                    observacoes=observacoes,
                    desconto=desconto,
                    valor_total=valor_total,
                    status='PENDENTE',
                    veiculo=veiculo_obj 
                )

                if cliente_id:
                    orcamento.cliente = Cliente.objects.filter(id=cliente_id).first()
                elif nome_avulso:
                    orcamento.nome_cliente_avulso = nome_avulso.upper()
                
                orcamento.save()

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

@login_required
def detalhe_orcamento(request, id):
    orcamento = get_object_or_404(Orcamento, id=id, despachante=request.user.perfilusuario.despachante)
    return render(request, 'financeiro/detalhe_orcamento.html', {'orcamento': orcamento})

@login_required
def aprovar_orcamento(request, id):
    orcamento = get_object_or_404(Orcamento, id=id, despachante=request.user.perfilusuario.despachante)
    despachante = request.user.perfilusuario.despachante

    if orcamento.status == 'APROVADO':
        messages.warning(request, "Este orçamento já foi aprovado.")
        return redirect('detalhe_orcamento', id=id)

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

            valor_impostos = total_honorarios_brutos * (despachante.aliquota_imposto / 100)
            valor_taxa_bancaria = total_honorarios_brutos * (despachante.taxa_bancaria_padrao / 100)

            nome_servico_agrupado = " + ".join(lista_nomes_servicos)[:100]
            obs_final = f"Gerado via Orçamento #{orcamento.id}.\nItens:\n" + "\n".join(detalhes_itens) + f"\n\nObs: {orcamento.observacoes or ''}"

            Atendimento.objects.create(
                despachante=orcamento.despachante,
                cliente=orcamento.cliente,
                veiculo=orcamento.veiculo,
                servico=nome_servico_agrupado,
                
                valor_taxas_detran=total_taxas_detran,
                valor_honorarios=total_honorarios_brutos,
                custo_impostos=valor_impostos,
                custo_taxa_bancaria=valor_taxa_bancaria,
                status_financeiro='ABERTO', 
                quem_pagou_detran='DESPACHANTE',
                
                status='SOLICITADO',
                data_solicitacao=timezone.now().date(),
                responsavel=request.user,
                observacoes_internas=obs_final
            )

        messages.success(request, f"Orçamento Aprovado!")
        return redirect('dashboard')

    except Exception as e:
        messages.error(request, f"Erro ao gerar processo: {e}")
        return redirect('detalhe_orcamento', id=id)

@login_required
def listar_orcamentos(request):
    termo = request.GET.get('termo', '').strip()
    status_filtro = request.GET.get('status')
    perfil = getattr(request.user, 'perfilusuario', None)
    if not perfil:
        return redirect('logout')

    orcamentos = Orcamento.objects.filter(
        despachante=perfil.despachante
    ).select_related('cliente', 'veiculo').prefetch_related('itens').order_by('-data_criacao')
    
    if termo:
        filtros = (
            Q(cliente__nome__icontains=termo) |
            Q(cliente__cpf_cnpj__icontains=termo) |
            Q(nome_cliente_avulso__icontains=termo) |
            Q(veiculo__placa__icontains=termo) |
            Q(veiculo__modelo__icontains=termo)
        )
        if termo.isdigit():
            filtros |= Q(id=termo)
        orcamentos = orcamentos.filter(filtros)
    
    if status_filtro:
        orcamentos = orcamentos.filter(status=status_filtro)
        
    return render(request, 'financeiro/lista_orcamentos.html', {'orcamentos': orcamentos, 'filters': request.GET})

@login_required
def excluir_orcamento(request, id):
    try:
        perfil = request.user.perfilusuario
    except:
        return redirect('dashboard')

    orcamento = get_object_or_404(Orcamento, id=id, despachante=perfil.despachante)
    
    if not request.user.is_superuser and perfil.tipo_usuario != 'ADMIN':
        if orcamento.status == 'APROVADO':
            messages.error(request, "⛔ Permissão Negada: Operadores não podem excluir orçamentos já APROVADOS.")
            return redirect('listar_orcamentos')

    if request.method == 'POST':
        orcamento.delete()
        messages.success(request, f"Orçamento #{id} excluído com sucesso.")
        return redirect('listar_orcamentos')
    
    return redirect('listar_orcamentos')

# ==============================================================================
# RELATÓRIOS
# ==============================================================================
@login_required
def relatorio_mensal(request):
    despachante = request.user.perfilusuario.despachante
    
    hoje = timezone.now().date()
    data_inicio_padrao = hoje.replace(day=1).strftime('%Y-%m-%d')
    data_fim_padrao = hoje.strftime('%Y-%m-%d')

    data_inicio = request.GET.get('data_inicio', data_inicio_padrao)
    data_fim = request.GET.get('data_fim', data_fim_padrao)
    cliente_placa = request.GET.get('cliente_placa')
    responsavel_id = request.GET.get('responsavel')

    processos = Atendimento.objects.filter(despachante=despachante).select_related('cliente', 'veiculo', 'responsavel').order_by('-data_solicitacao')

    if data_inicio and data_fim:
        processos = processos.filter(data_solicitacao__range=[data_inicio, data_fim])
    
    if cliente_placa:
        processos = processos.filter(Q(cliente__nome__icontains=cliente_placa) | Q(veiculo__placa__icontains=cliente_placa))
    
    if responsavel_id:
        processos = processos.filter(responsavel_id=responsavel_id)

    resumo_raw = processos.values('status').annotate(total=Count('id'))
    status_dict = dict(Atendimento.STATUS_CHOICES)
    
    resumo_status = []
    for item in resumo_raw:
        resumo_status.append({'status': status_dict.get(item['status'], item['status']), 'total': item['total']})

    equipe = PerfilUsuario.objects.filter(despachante=despachante).select_related('user')

    context = {
        'processos': processos,
        'equipe': equipe,
        'resumo_status': resumo_status,
        'total_qtd': processos.count(),
        'filtros': {'data_inicio': data_inicio, 'data_fim': data_fim, 'cliente_placa': cliente_placa, 'responsavel': responsavel_id}
    }
    
    return render(request, 'cadastro/relatorio_mensal.html', context)

@login_required
def relatorio_servicos(request):
    data_inicio = request.GET.get('data_inicio')
    data_fim = request.GET.get('data_fim')
    cliente_placa = request.GET.get('cliente_placa')
    status_fin = request.GET.get('status_financeiro')

    relatorio_agrupado = None
    total_geral_taxas = 0
    total_geral_honorarios = 0
    total_geral_valor = 0

    if cliente_placa:
        atendimentos = Atendimento.objects.filter(
            despachante=request.user.perfilusuario.despachante,
            status='APROVADO' 
        ).select_related('cliente', 'veiculo').order_by('cliente__nome', '-data_solicitacao')

        if data_inicio: atendimentos = atendimentos.filter(data_solicitacao__gte=data_inicio)
        if data_fim: atendimentos = atendimentos.filter(data_solicitacao__lte=data_fim)
        if status_fin: atendimentos = atendimentos.filter(status_financeiro=status_fin)

        atendimentos = atendimentos.filter(Q(cliente__nome__icontains=cliente_placa) | Q(veiculo__placa__icontains=cliente_placa))

        relatorio_agrupado = {}
        
        for item in atendimentos:
            taxas = item.valor_taxas_detran or 0
            honorarios = item.valor_honorarios or 0
            valor_total_item = taxas + honorarios
            
            placa = item.veiculo.placa if item.veiculo else "S/P"
            modelo = item.veiculo.modelo if item.veiculo else "---"

            cliente_id = item.cliente.id
            if cliente_id not in relatorio_agrupado:
                tel_bruto = item.cliente.telefone or ""
                tel_limpo = "".join([c for c in tel_bruto if c.isdigit()])

                relatorio_agrupado[cliente_id] = {
                    'dados_cliente': item.cliente,
                    'telefone_limpo': tel_limpo,
                    'itens': [],      
                    'linhas_zap': [], 
                    'texto_whatsapp': '', 
                    'subtotal_taxas': 0,
                    'subtotal_honorarios': 0,
                    'subtotal_valor': 0
                }

            relatorio_agrupado[cliente_id]['itens'].append({
                'id': item.id,
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

            linha_formatada = f"• {item.servico} ({placa}) - R$ {valor_total_item:.2f}"
            relatorio_agrupado[cliente_id]['linhas_zap'].append(linha_formatada)

            relatorio_agrupado[cliente_id]['subtotal_taxas'] += taxas
            relatorio_agrupado[cliente_id]['subtotal_honorarios'] += honorarios
            relatorio_agrupado[cliente_id]['subtotal_valor'] += valor_total_item

            total_geral_taxas += taxas
            total_geral_honorarios += honorarios
            total_geral_valor += valor_total_item

        for c_id, dados in relatorio_agrupado.items():
            nome_cliente = dados['dados_cliente'].nome.split()[0] if dados['dados_cliente'].nome else "Cliente"
            total_formatado = f"{dados['subtotal_valor']:.2f}"
            lista_servicos = "\n".join(dados['linhas_zap'])
            msg = f"Olá {nome_cliente}, segue o extrato dos seus serviços:\n\n{lista_servicos}\n\n*TOTAL A PAGAR: R$ {total_formatado}*"
            dados['texto_whatsapp'] = msg

    context = {
        'relatorio_agrupado': relatorio_agrupado,
        'total_geral_taxas': total_geral_taxas,
        'total_geral_honorarios': total_geral_honorarios,
        'total_geral_valor': total_geral_valor,
        'filtros': request.GET
    }
    return render(request, 'relatorios/relatorio_servicos.html', context)

@login_required
def fluxo_caixa(request):
    despachante = request.user.perfilusuario.despachante
    
    data_inicio = request.GET.get('data_inicio')
    data_fim = request.GET.get('data_fim')
    cliente_nome = request.GET.get('cliente')
    status_fin = request.GET.get('status_financeiro')

    processos = Atendimento.objects.filter(despachante=despachante, status='APROVADO').select_related('cliente', 'veiculo').order_by('-data_solicitacao')

    if not any([data_inicio, data_fim, cliente_nome, status_fin]):
        hoje = timezone.now().date()
        processos = processos.filter(data_solicitacao__month=hoje.month, data_solicitacao__year=hoje.year)
    else:
        if data_inicio: processos = processos.filter(data_solicitacao__gte=data_inicio)
        if data_fim: processos = processos.filter(data_solicitacao__lte=data_fim)
        if cliente_nome: processos = processos.filter(Q(cliente__nome__icontains=cliente_nome) | Q(veiculo__placa__icontains=cliente_nome))
        if status_fin: processos = processos.filter(status_financeiro=status_fin)

    dados_financeiros = processos.aggregate(
        total_taxas=Sum('valor_taxas_detran'),
        total_honorarios=Sum('valor_honorarios'),
        total_impostos=Sum('custo_impostos'),
        total_bancario=Sum('custo_taxa_bancaria')
    )

    resumo = {
        'total_pendentes': processos.filter(status_financeiro='ABERTO').count(),
        'valor_taxas': dados_financeiros['total_taxas'] or 0,
        'valor_honorarios_bruto': dados_financeiros['total_honorarios'] or 0,
        'valor_impostos': dados_financeiros['total_impostos'] or 0,
        'valor_bancario': dados_financeiros['total_bancario'] or 0,
    }
    
    resumo['faturamento_total'] = resumo['valor_taxas'] + resumo['valor_honorarios_bruto']
    resumo['total_custos_operacionais'] = resumo['valor_impostos'] + resumo['valor_bancario']
    resumo['lucro_liquido_total'] = resumo['valor_honorarios_bruto'] - resumo['total_custos_operacionais']

    return render(request, 'cadastro/fluxo_caixa.html', {'processos': processos, 'resumo': resumo, 'filtros': request.GET})

@login_required
def dar_baixa_pagamento(request, id):
    processo = get_object_or_404(Atendimento, id=id, despachante=request.user.perfilusuario.despachante)
    processo.status_financeiro = 'PAGO'
    processo.data_pagamento = timezone.now().date()
    processo.save()
    messages.success(request, f"Recebimento confirmado!")
    return redirect('fluxo_caixa')

@login_required
def dashboard_financeiro(request):
    despachante = request.user.perfilusuario.despachante
    processos_fin = Atendimento.objects.filter(despachante=despachante, status='APROVADO').exclude(status='CANCELADO')

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

    pie_data = [float(lucro_liquido), float(impostos), float(bancario)]

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

    agregados = devedores_qs.aggregate(total_taxas=Sum('valor_taxas_detran'), total_honorarios=Sum('valor_honorarios'))
    total_taxas = agregados['total_taxas'] or 0
    total_honorarios = agregados['total_honorarios'] or 0

    lista_devedores = []
    for item in devedores_qs:
        dias_atraso = (hoje - item.data_solicitacao).days
        valor_total_calc = (item.valor_taxas_detran or 0) + (item.valor_honorarios or 0)
        tel_bruto = item.cliente.telefone or ""
        telefone_limpo = "".join([c for c in tel_bruto if c.isdigit()])
        primeiro_nome = item.cliente.nome.split()[0] if item.cliente.nome else "Cliente"
        placa = item.veiculo.placa if item.veiculo else "S/P"
        texto_whatsapp = f"Olá {primeiro_nome}, identificamos uma pendência referente ao serviço de {item.servico} (Placa: {placa}).\nValor em aberto: R$ {valor_total_calc:.2f}.\nPodemos agendar o pagamento?"

        lista_devedores.append({
            'id': item.id,
            'dias_atraso': dias_atraso,
            'cliente': item.cliente,
            'servico': item.servico,
            'veiculo': item.veiculo,
            'valor_taxas_detran': item.valor_taxas_detran,
            'valor_honorarios': item.valor_honorarios,
            'valor_total_cliente': valor_total_calc,
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

    return render(request, 'cadastro/configuracoes_despachante.html', {'despachante': despachante})

@login_required
def emitir_recibo(request, id):
    atendimento = get_object_or_404(Atendimento, id=id, despachante=request.user.perfilusuario.despachante)
    taxas = atendimento.valor_taxas_detran or 0
    honorarios = atendimento.valor_honorarios or 0
    total = taxas + honorarios
    context = {'atendimento': atendimento, 'taxas': taxas, 'honorarios': honorarios, 'total': total, 'data_atual': timezone.now().date()}
    return render(request, 'cadastro/recibo_impressao.html', context)

# ==============================================================================
# IMPRESSÃO DE DOCUMENTOS
# ==============================================================================
@login_required
def selecao_documento(request):
    despachante_logado = request.user.perfilusuario.despachante
    clientes = Cliente.objects.filter(despachante=despachante_logado).order_by('nome')
    servicos = TipoServico.objects.filter(despachante=despachante_logado, ativo=True)
    return render(request, 'documentos/selecao_documento.html', {'clientes': clientes, 'servicos': servicos})

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
    return {
        'nome': pessoa.nome.upper(),
        'cpf_cnpj': pessoa.cpf_cnpj,
        'doc': pessoa.cpf_cnpj,
        'rg': f"{pessoa.rg or ''} {pessoa.orgao_expedidor or ''}",
        'endereco': f"{pessoa.rua}, {pessoa.numero}, {pessoa.bairro}",
        'cidade': pessoa.cidade,
        'uf': pessoa.uf,
        'cep': pessoa.cep,
        'email': pessoa.email,
        'telefone': pessoa.telefone
    }

def _imagem_para_base64(imagem_upload):
    try:
        if not imagem_upload: return None
        imagem_bytes = imagem_upload.read()
        imagem_b64 = base64.b64encode(imagem_bytes).decode('utf-8')
        return f"data:{imagem_upload.content_type};base64,{imagem_b64}"
    except:
        return None

@login_required
def imprimir_documento(request):
    if request.method == 'POST':
        tipo_doc = request.POST.get('tipo_documento')
        cliente_id = request.POST.get('cliente_id')
        veiculo_placa = request.POST.get('veiculo_placa')
        servicos_selecionados_ids = request.POST.getlist('servicos_selecionados')
        motivo_2via = request.POST.get('motivo_2via')
        alteracao_pretendida = request.POST.get('alteracao_pretendida')
        valor_recibo = request.POST.get('valor_recibo')
        tipo_outorgado = request.POST.get('tipo_outorgado') 
        outorgado_id = request.POST.get('outorgado_id')
        comprador_id = request.POST.get('comprador_id')
        valor_venda = request.POST.get('valor_venda')
        numero_crv = request.POST.get('numero_crv')
        numero_atpv = request.POST.get('numero_atpv')
        motivo_baixa = request.POST.get('motivo_baixa')
        tipo_solicitante_baixa = request.POST.get('tipo_solicitante_baixa')
        possui_procurador_baixa = request.POST.get('possui_procurador_baixa') 

        despachante_obj = request.user.perfilusuario.despachante
        cliente = get_object_or_404(Cliente, id=cliente_id, despachante=despachante_obj)
        
        veiculo = None
        if veiculo_placa:
            veiculo = Veiculo.objects.filter(placa=veiculo_placa, cliente=cliente).first()

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

        comprador_dados = {}
        if tipo_doc == 'procuracao_atpv' and comprador_id:
            try:
                comp = Cliente.objects.get(id=comprador_id, despachante=despachante_obj)
                comprador_dados = _formatar_dados_pessoa(comp)
            except Cliente.DoesNotExist:
                comprador_dados = {'nome': 'COMPRADOR NÃO ENCONTRADO'}

        fotos_processadas = []
        for i in range(1, 5): 
            campo_foto = f'foto{i}'
            if campo_foto in request.FILES:
                img_b64 = _imagem_para_base64(request.FILES[campo_foto])
                fotos_processadas.append(img_b64)
            else:
                fotos_processadas.append(None)

        lista_nomes_servicos = []
        if servicos_selecionados_ids:
            servicos_objs = TipoServico.objects.filter(id__in=servicos_selecionados_ids)
            lista_nomes_servicos = [s.nome for s in servicos_objs]
        texto_servicos = ", ".join(lista_nomes_servicos) if lista_nomes_servicos else "______________________________________________________"

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
            'transacao': {'valor': valor_venda, 'crv': numero_crv, 'atpv': numero_atpv},
            'motivo_baixa': motivo_baixa,
            'tipo_solicitante_baixa': tipo_solicitante_baixa,
            'possui_procurador_baixa': possui_procurador_baixa,
            'fotos': fotos_processadas
        }

        templates_doc = {
            'procuracao': 'documentos/print_procuracao.html',
            'procuracao_atpv': 'documentos/print_procuracao_atpv.html',
            'procuracao_particular': 'documentos/print_procuracao_particular.html',
            'declaracao': 'documentos/print_declaracao.html',
            'requerimento_2via': 'documentos/print_requerimento_2via.html',
            'alteracao_caracteristica': 'documentos/print_alteracao_caracteristica.html',
            'recibo': 'documentos/print_recibo.html',
            'contrato': 'documentos/print_contrato.html',    
            'alteracao_endereco': 'documentos/print_alteracao_endereco.html',
            'requerimento_baixa': 'documentos/print_requerimento_baixa.html',
            'termo_fotografico_veiculo': 'documentos/print_termo_fotografico_veiculo.html',
            'termo_fotografico_placas': 'documentos/print_termo_fotografico_placas.html',
        }

        template_name = templates_doc.get(tipo_doc)
        if template_name:
            return render(request, template_name, context)
    
    return redirect('selecao_documento')

@login_required
def ferramentas_compressao(request):
    if request.method == 'POST':
        form = CompressaoPDFForm(request.POST, request.FILES)
        if form.is_valid():
            arquivo = request.FILES['arquivo_pdf']
            pdf_pronto = comprimir_pdf_memoria(arquivo)
            if pdf_pronto:
                return FileResponse(pdf_pronto, as_attachment=True, filename=f"Otimizado_{arquivo.name}")
            else:
                messages.error(request, "Não foi possível comprimir este arquivo.")
    else:
        form = CompressaoPDFForm()
    return render(request, 'ferramentas/compressao.html', {'form': form})

# ==============================================================================
# PAINEL MASTER (SaaS)
# ==============================================================================
def is_master(user):
    return user.is_superuser

@login_required
@user_passes_test(is_master)
def financeiro_master(request):
    despachantes = Despachante.objects.all().order_by('nome_fantasia')
    lista_financeira = []
    total_receita_mensal = 0
    total_inadimplentes = 0
    
    for d in despachantes:
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

@login_required
@user_passes_test(is_master)
def acao_cobrar_cliente(request, despachante_id):
    despachante = get_object_or_404(Despachante, id=despachante_id)
    resultado = gerar_boleto_asaas(despachante)
    if resultado['sucesso']:
        messages.success(request, f"Cobrança gerada! Link: {resultado['link_fatura']}")
    else:
        messages.error(request, f"Erro ao cobrar: {resultado.get('erro')}")
    return redirect('financeiro_master')

@login_required
@user_passes_test(is_master)
def acao_liberar_acesso(request, despachante_id):
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
    messages.success(request, f"Acesso liberado por +20 dias para {count} usuários.")
    return redirect('financeiro_master')

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
        form = DespachanteForm(request.POST, request.FILES, instance=despachante)
        if form.is_valid():
            form.save()
            messages.success(request, "Dados do despachante salvos!")
            return redirect('master_listar_despachantes')
    else:
        form = DespachanteForm(instance=despachante)

    return render(request, 'master/form_despachante.html', {'form': form, 'titulo': titulo})

@login_required
@user_passes_test(is_master)
def master_listar_usuarios(request):
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
                    login_digitado = form.cleaned_data.get('username')
                    email_digitado = form.cleaned_data['email']
                    username_final = login_digitado if login_digitado else email_digitado

                    novo_user = User.objects.create(
                        username=username_final,
                        email=email_digitado,
                        first_name=form.cleaned_data['first_name'],
                        last_name=form.cleaned_data['last_name'],
                        password=make_password(form.cleaned_data['password'])
                    )
                    
                    PerfilUsuario.objects.create(
                        user=novo_user,
                        despachante=form.cleaned_data['despachante'],
                        tipo_usuario=form.cleaned_data['tipo_usuario'],
                        pode_fazer_upload=True
                    )
                    
                messages.success(request, f"Usuário criado! Login: {username_final}")
                return redirect('master_listar_usuarios')
            except Exception as e:
                messages.error(request, f"Erro: Login ou E-mail já em uso.")
    else:
        form = UsuarioMasterForm()
    return render(request, 'master/form_usuario.html', {'form': form})

@login_required
@user_passes_test(is_master)
def master_editar_usuario(request, id):
    user_edit = get_object_or_404(User, id=id)
    try:
        perfil = user_edit.perfilusuario
    except PerfilUsuario.DoesNotExist:
        perfil = None

    if request.method == 'POST':
        form = UsuarioMasterEditForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    user_edit.first_name = form.cleaned_data['first_name']
                    user_edit.last_name = form.cleaned_data['last_name']
                    
                    nova_senha = form.cleaned_data['password']
                    if nova_senha:
                        user_edit.password = make_password(nova_senha)
                    user_edit.save()

                    if not perfil:
                        perfil = PerfilUsuario(user=user_edit)
                    
                    perfil.despachante = form.cleaned_data['despachante']
                    perfil.tipo_usuario = form.cleaned_data['tipo_usuario']
                    perfil.save()

                messages.success(request, "Usuário atualizado!")
                return redirect('master_listar_usuarios')
            except Exception as e:
                messages.error(request, f"Erro ao atualizar: {e}")
    else:
        initial_data = {
            'first_name': user_edit.first_name,
            'last_name': user_edit.last_name,
            'email': user_edit.email,
            'despachante': perfil.despachante if perfil else None,
            'tipo_usuario': perfil.tipo_usuario if perfil else 'OPERAR',
        }
        form = UsuarioMasterEditForm(initial=initial_data)

    return render(request, 'master/form_usuario.html', {'form': form, 'titulo': f"Editar Usuário: {user_edit.first_name}"})