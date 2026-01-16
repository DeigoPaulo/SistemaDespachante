import os
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils import timezone
from django.db.models import Q, Sum, Count, Value, DecimalField
from django.db import transaction
from django.http import JsonResponse, FileResponse
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password
from django.urls import reverse
from django.db.models.functions import ExtractMonth, Coalesce
import re
import json
import base64
from decimal import Decimal
from datetime import timedelta
from django.core.paginator import Paginator
import requests 
from decimal import Decimal, InvalidOperation
from django.db.models import Q, Sum, Count, Value, DecimalField, F, ExpressionWrapper
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.conf import settings
from .models import BaseConhecimento
from .forms import BaseConhecimentoForm
from groq import Groq




# Importação dos Modelos e Forms
from .models import Atendimento, Cliente, Veiculo, TipoServico, PerfilUsuario, Despachante, Orcamento, ItemOrcamento, LogAtividade
from .forms import AtendimentoForm, ClienteForm, VeiculoForm, DespachanteForm, UsuarioMasterForm, UsuarioMasterEditForm, CompressaoPDFForm
from .asaas import gerar_boleto_asaas
from .utils import comprimir_pdf_memoria, registrar_log

# --- FUNÇÃO DE SEGURANÇA ---
def is_admin_or_superuser(user):
    """
    Retorna True se o usuário for Superusuário ou tiver perfil 'ADMIN'.
    """
    if not user.is_authenticated:
        return False
        
    if user.is_superuser:
        return True
        
    if hasattr(user, 'perfilusuario') and user.perfilusuario.tipo_usuario == 'ADMIN':
        return True
        
    return False

# ==============================================================================
# 1. LOGIN E AUTENTICAÇÃO
# ==============================================================================
def minha_view_de_login(request):
    contexto = {'erro_login': False}

    if request.method == 'POST':
        login_input = request.POST.get('username') 
        password_form = request.POST.get('password')
        
        username_para_autenticar = login_input

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

    status_finalizados = ['APROVADO', 'CANCELADO', 'CONCLUIDO', 'ENTREGUE']

    hoje = timezone.now().date()
    
    total_abertos = Atendimento.objects.filter(
        despachante=despachante
    ).exclude(
        status__in=status_finalizados
    ).count()

    total_mes = Atendimento.objects.filter(
        despachante=despachante, 
        data_solicitacao__month=hoje.month,
        data_solicitacao__year=hoje.year
    ).count()

    # Otimização: select_related para evitar query N+1
    fila_processos = Atendimento.objects.select_related(
        'cliente', 'veiculo', 'responsavel', 'tipo_servico'
    ).filter(
        despachante=despachante
    ).exclude(
        status__in=status_finalizados
    ).order_by('data_solicitacao')
    
    if data_filtro:
        fila_processos = fila_processos.filter(data_solicitacao=data_filtro)

    if termo_busca:
        fila_processos = fila_processos.filter(
            Q(cliente__nome__icontains=termo_busca) |
            Q(veiculo__placa__icontains=termo_busca) |
            Q(numero_atendimento__icontains=termo_busca) |
            Q(servico__icontains=termo_busca)
        )
    
    # Paginação para evitar travamento (50 itens por página)
    paginator = Paginator(fila_processos, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Lógica de Cores (Frontend Logic)
    for processo in page_obj:
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
            dias_corridos = (hoje - processo.data_solicitacao).days
            if dias_corridos >= 30:
                processo.alerta_cor = 'danger'
            elif dias_corridos >= 15:
                processo.alerta_cor = 'warning'
            else:
                processo.alerta_cor = 'success'

    context = {
        'fila_processos': page_obj, # Passa o objeto paginado
        'total_abertos': total_abertos, 
        'total_mes': total_mes,
        'perfil': perfil,
        'data_filtro': data_filtro,
        'termo_busca': termo_busca,
    }
    
    return render(request, 'dashboard.html', context)

# ==============================================================================
# GESTÃO DE ATENDIMENTOS (CRUD) - REFATORADO PARA MODEL INTELIGENTE
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
            
            if not atendimento.responsavel:
                atendimento.responsavel = request.user
            
            # --- CORREÇÃO FINANCEIRA COMPLETA (DECIMAL) ---
            if atendimento.tipo_servico:
                # 1. Se valores estiverem zerados, puxa do catálogo
                if not atendimento.valor_taxas_detran:
                    atendimento.valor_taxas_detran = atendimento.tipo_servico.valor_base
                
                if not atendimento.valor_honorarios:
                    atendimento.valor_honorarios = atendimento.tipo_servico.honorarios
                
                # 2. Lógica da Taxa Sindego (FALTAVA ISSO!)
                if atendimento.custo_taxa_sindego == 0:
                    if atendimento.tipo_servico.usa_taxa_sindego_reduzida:
                        atendimento.custo_taxa_sindego = despachante.valor_taxa_sindego_reduzida
                    else:
                        atendimento.custo_taxa_sindego = despachante.valor_taxa_sindego_padrao

            # 3. Recalcula Custos Variáveis (Imposto e Banco) com precisão
            val_honorarios = Decimal(str(atendimento.valor_honorarios or 0))
            val_taxas = Decimal(str(atendimento.valor_taxas_detran or 0))
            
            aliq_imposto = Decimal(str(despachante.aliquota_imposto or 0))
            aliq_banco = Decimal(str(despachante.taxa_bancaria_padrao or 0))

            # Imposto incide apenas sobre Honorários
            atendimento.custo_impostos = val_honorarios * (aliq_imposto / 100)
            
            # Taxa bancária incide sobre o total transacionado
            total_transacao = val_taxas + val_honorarios
            atendimento.custo_taxa_bancaria = total_transacao * (aliq_banco / 100)

            atendimento.save()

            registrar_log(
                request, 
                'CRIACAO', 
                f"Criou o processo #{atendimento.numero_atendimento} ({atendimento.servico}) para {atendimento.cliente}.",
                atendimento=atendimento,
                cliente=atendimento.cliente
            )

            messages.success(request, "Processo criado com financeiro corrigido!")
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
            
            # --- FORÇA RECALCULO FINANCEIRO AO EDITAR ---
            # Converte para Decimal
            val_honorarios = Decimal(str(atendimento_obj.valor_honorarios or 0))
            val_taxas = Decimal(str(atendimento_obj.valor_taxas_detran or 0))
            
            aliq_imposto = Decimal(str(despachante.aliquota_imposto or 0))
            aliq_banco = Decimal(str(despachante.taxa_bancaria_padrao or 0))

            # Recalcula custos proporcionais
            atendimento_obj.custo_impostos = val_honorarios * (aliq_imposto / 100)
            
            total_transacao = val_taxas + val_honorarios
            atendimento_obj.custo_taxa_bancaria = total_transacao * (aliq_banco / 100)
            
            # Nota: A taxa do sindicato (sindego) geralmente é fixa, então não recalculamos
            # automaticamente na edição para não sobrescrever caso seja uma exceção manual.
            # Mas garantimos que não fique zerada se foi esquecida.
            if atendimento_obj.custo_taxa_sindego == 0 and atendimento_obj.tipo_servico:
                 if atendimento_obj.tipo_servico.usa_taxa_sindego_reduzida:
                     atendimento_obj.custo_taxa_sindego = despachante.valor_taxa_sindego_reduzida
                 else:
                     atendimento_obj.custo_taxa_sindego = despachante.valor_taxa_sindego_padrao

            atendimento_obj.save()
            
            registrar_log(
                request, 
                'EDICAO', 
                f"Editou o processo #{atendimento_obj.numero_atendimento}. Status: {atendimento_obj.get_status_display()}.",
                atendimento=atendimento_obj,
                cliente=atendimento_obj.cliente
            )

            messages.success(request, f"Processo e financeiro atualizados!")
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
        num = atendimento.numero_atendimento
        nome = atendimento.cliente.nome if atendimento.cliente else "Desconhecido"
        
        registrar_log(
            request, 
            'EXCLUSAO', 
            f"Excluiu permanentemente o processo #{num} de {nome}.",
            cliente=atendimento.cliente 
        )

        atendimento.delete()
        messages.success(request, "Processo removido com sucesso.")
        return redirect('dashboard')

    return redirect('dashboard')

@login_required
def imprimir_capa_processo(request, id):
    atendimento = get_object_or_404(Atendimento, id=id, despachante=request.user.perfilusuario.despachante)

    # Lógica: Se enviou um POST, é porque está salvando o número
    if request.method == 'POST':
        novo_numero = request.POST.get('numero_atendimento')
        if novo_numero:
            atendimento.numero_atendimento = novo_numero
            atendimento.save()
            # Redireciona para a mesma página (GET) para imprimir agora
            return redirect('imprimir_capa_processo', id=id)

    # Se NÃO tem número gravado, mostra tela de bloqueio pedindo o número
    if not atendimento.numero_atendimento:
        return render(request, 'documentos/bloqueio_capa.html', {'atendimento': atendimento})

    # Se JÁ tem número, renderiza a capa A4
    context = {
        'atendimento': atendimento,
        'hoje': timezone.now()
    }
    return render(request, 'documentos/print_capa_processo.html', context)

# ==============================================================================
# CADASTRO RÁPIDO (LOTE)
# ==============================================================================

from decimal import Decimal
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
                # --- [Bloco de Responsável e Cliente] (Mantido) ---
                responsavel_id = request.POST.get('responsavel') or request.POST.get('responsavel_id')
                responsavel_obj = request.user 
                if responsavel_id:
                    try:
                        responsavel_obj = User.objects.get(id=responsavel_id)
                    except User.DoesNotExist:
                        pass

                cliente_id = request.POST.get('cliente_id')
                if not cliente_id:
                    messages.error(request, "Nenhum cliente selecionado.")
                    return redirect('cadastro_rapido')
                
                cliente = get_object_or_404(Cliente, id=cliente_id, despachante=despachante)

                # --- Listas do Formulário ---
                placas = request.POST.getlist('veiculo_placa[]')
                modelos = request.POST.getlist('veiculo_modelo[]')
                servicos_str_lista = request.POST.getlist('servico[]') 
                atendimentos = request.POST.getlist('numero_atendimento[]')
                
                obs_geral = request.POST.get('observacoes', '')
                prazo_input = request.POST.get('prazo_entrega')

                # LISTA PARA O RESUMO (NOVIDADE)
                processos_criados = []

                for i in range(len(placas)):
                    placa_limpa = placas[i].replace('-', '').replace(' ', '').upper()
                    if not placa_limpa: continue

                    # 1. Cria ou Pega o Veículo (Mantido)
                    veiculo, _ = Veiculo.objects.get_or_create(
                        placa=placa_limpa,
                        despachante=despachante,
                        defaults={'cliente': cliente, 'modelo': modelos[i].upper()}
                    )

                    # 2. SOMATÓRIA DOS SERVIÇOS (Mantido)
                    nomes_selecionados = [s.strip() for s in servicos_str_lista[i].split('+')]
                    
                    total_taxas_detran = Decimal('0.00')
                    total_honorarios = Decimal('0.00')
                    total_custo_sindego = Decimal('0.00')
                    tipo_servico_vinculado = None

                    for nome_s in nomes_selecionados:
                        s_base = servicos_db.filter(nome__iexact=nome_s).first()
                        
                        if s_base:
                            total_taxas_detran += s_base.valor_base
                            total_honorarios += s_base.honorarios

                            if s_base.usa_taxa_sindego_reduzida:
                                total_custo_sindego += despachante.valor_taxa_sindego_reduzida
                            else:
                                total_custo_sindego += despachante.valor_taxa_sindego_padrao

                            if len(nomes_selecionados) == 1:
                                tipo_servico_vinculado = s_base

                    # 3. CÁLCULO DE IMPOSTOS (Mantido)
                    aliquota_db = Decimal(str(despachante.aliquota_imposto or 0))
                    taxa_maq_db = Decimal(str(despachante.taxa_bancaria_padrao or 0))
                    
                    custo_imp = total_honorarios * (aliquota_db / 100)
                    custo_ban = total_honorarios * (taxa_maq_db / 100)

                    # 4. CRIAÇÃO DO ATENDIMENTO (Mantido)
                    atendimento = Atendimento.objects.create(
                        despachante=despachante,
                        cliente=cliente,
                        veiculo=veiculo,
                        tipo_servico=tipo_servico_vinculado, 
                        servico=servicos_str_lista[i], 
                        responsavel=responsavel_obj,
                        numero_atendimento=atendimentos[i] if i < len(atendimentos) else '',
                        
                        valor_taxas_detran=total_taxas_detran,
                        valor_honorarios=total_honorarios,
                        
                        custo_impostos=custo_imp,
                        custo_taxa_bancaria=custo_ban,
                        custo_taxa_sindego=total_custo_sindego,
                        
                        status_financeiro='ABERTO',
                        status='SOLICITADO',
                        data_solicitacao=timezone.now().date(),
                        data_entrega=prazo_input if prazo_input else None,
                        observacoes_internas=f"{obs_geral}\nGerado via Cadastro Rápido."
                    )
                    
                    # ADICIONA À LISTA DE RESUMO
                    processos_criados.append(atendimento)

            # --- REDIRECIONAMENTO NOVO ---
            # Em vez de voltar pro dashboard, vai pro resumo pra imprimir capas
            return render(request, 'processos/resumo_lote.html', {
                'processos': processos_criados,
                'qtd': len(processos_criados)
            })

        except Exception as e:
            print(f"ERRO CRÍTICO NO CADASTRO RÁPIDO: {e}")
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

                # 1. TRATAMENTO DA DATA DE NASCIMENTO
                data_nasc = request.POST.get('data_nascimento')
                if not data_nasc: 
                    data_nasc = None

                # 2. CRIA OU RECUPERA O CLIENTE
                cliente, created = Cliente.objects.get_or_create(
                    cpf_cnpj=cpf_cnpj_raw, 
                    despachante=despachante,
                    defaults={
                        'nome': request.POST.get('cliente_nome'),
                        'telefone': request.POST.get('cliente_telefone'),
                        'email': request.POST.get('cliente_email'),
                        'rg': request.POST.get('rg'),
                        'data_nascimento': data_nasc,  # <--- Salva a data
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

                # Se o cliente já existia, atualiza os dados principais
                if not created:
                    cliente.nome = request.POST.get('cliente_nome')
                    cliente.telefone = request.POST.get('cliente_telefone')
                    cliente.email = request.POST.get('cliente_email')
                    if data_nasc:
                        cliente.data_nascimento = data_nasc
                    cliente.save()

                # 3. SALVAMENTO COMPLETO DOS VEÍCULOS
                # Captura todas as listas enviadas pelo JavaScript
                placas = request.POST.getlist('veiculo_placa[]')
                modelos = request.POST.getlist('veiculo_modelo[]')
                renavams = request.POST.getlist('veiculo_renavam[]')
                chassis = request.POST.getlist('veiculo_chassi[]')     # Novo
                marcas = request.POST.getlist('veiculo_marca[]')       # Novo
                cores = request.POST.getlist('veiculo_cor[]')          # Novo
                tipos = request.POST.getlist('veiculo_tipo[]')         # Novo
                anos_fab = request.POST.getlist('veiculo_ano_fabricacao[]') # Novo
                anos_mod = request.POST.getlist('veiculo_ano_modelo[]')     # Novo
                
                for i in range(len(placas)):
                    placa_limpa = placas[i].replace('-', '').replace(' ', '').upper()
                    if not placa_limpa: continue
                    if len(placa_limpa) > 7: placa_limpa = placa_limpa[:7]

                    # Helper para evitar erro de índice se a lista vier menor
                    def get_val(lista, index):
                        return lista[index] if index < len(lista) else ''

                    # Helper para converter ano em número ou None (evita erro de string vazia)
                    def get_int(lista, index):
                        val = lista[index] if index < len(lista) else ''
                        return int(val) if val.isdigit() else None

                    Veiculo.objects.get_or_create(
                        placa=placa_limpa,
                        despachante=despachante,
                        defaults={
                            'cliente': cliente, 
                            'modelo': get_val(modelos, i),
                            'renavam': get_val(renavams, i),
                            # Campos novos sendo salvos agora:
                            'chassi': get_val(chassis, i),
                            'marca': get_val(marcas, i),
                            'cor': get_val(cores, i),
                            'tipo': get_val(tipos, i),
                            'ano_fabricacao': get_int(anos_fab, i),
                            'ano_modelo': get_int(anos_mod, i),
                        }
                    )

            return redirect('dashboard')

        except Exception as e:
            # print(f"Erro: {e}") # Descomente para debug se precisar
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
    
    return render(request, 'veiculos/veiculo_form.html', {'form': form})

@login_required
def lista_clientes(request):
    perfil = request.user.perfilusuario
    # Otimização: Traz apenas o necessário
    clientes = Cliente.objects.filter(despachante=perfil.despachante).order_by('nome')
    search_term = request.GET.get('q')

    if search_term:
        clientes = clientes.filter(
            Q(nome__icontains=search_term) | 
            Q(cpf_cnpj__icontains=search_term) |
            Q(telefone__icontains=search_term)
        )
    
    # Paginação para evitar crash com muitos clientes
    paginator = Paginator(clientes, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'clientes/lista_clientes.html', {'clientes': page_obj})

@login_required
def detalhe_cliente(request, id):
    perfil = request.user.perfilusuario
    cliente = get_object_or_404(Cliente, id=id, despachante=perfil.despachante)
    # Usa o índice de veículo
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
@user_passes_test(is_admin_or_superuser, login_url='/dashboard/')
def gerenciar_servicos(request):
    perfil = request.user.perfilusuario
    servicos = TipoServico.objects.filter(despachante=perfil.despachante, ativo=True)
    
    if request.method == 'POST':
        nome = request.POST.get('nome')
        raw_base = request.POST.get('valor_base', '0')
        raw_hon = request.POST.get('honorarios', '0')
        
        # --- CAPTURA DAS OPÇÕES DE TAXA SINDICAL ---
        usa_reduzida = request.POST.get('usa_taxa_sindego_reduzida') == 'on'
        eh_isento = request.POST.get('isenta_taxa_sindego') == 'on' # <--- NOVO CAMPO

        # Tratamento seguro para moeda (pt-BR para float/decimal)
        v_base = raw_base.replace('.', '').replace(',', '.') if raw_base else 0
        v_hon = raw_hon.replace('.', '').replace(',', '.') if raw_hon else 0
        
        TipoServico.objects.create(
            despachante=perfil.despachante, 
            nome=nome, 
            valor_base=v_base, 
            honorarios=v_hon,
            usa_taxa_sindego_reduzida=usa_reduzida,
            isenta_taxa_sindego=eh_isento # <--- SALVANDO NO BANCO
        )
        
        messages.success(request, "Novo serviço cadastrado com sucesso!")
        return redirect('gerenciar_servicos')

    return render(request, 'gerenciar_servicos.html', {'servicos': servicos})

@login_required
@user_passes_test(is_admin_or_superuser, login_url='/dashboard/')
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
            
            # --- ATUALIZAÇÃO DAS TAXAS ---
            servico.usa_taxa_sindego_reduzida = request.POST.get('usa_taxa_sindego_reduzida') == 'on'
            servico.isenta_taxa_sindego = request.POST.get('isenta_taxa_sindego') == 'on' # <--- NOVO CAMPO
            
            servico.save()
            
            messages.success(request, f"Serviço '{servico.nome}' atualizado!")
            return redirect('gerenciar_servicos')
        except Exception as e:
            messages.error(request, "Erro ao atualizar valores. Verifique os campos.")

    return render(request, 'cadastro/editar_servico.html', {'servico': servico})

@login_required
@user_passes_test(is_admin_or_superuser, login_url='/dashboard/')
def excluir_servico(request, id):
    perfil = request.user.perfilusuario
    servico = get_object_or_404(TipoServico, id=id, despachante=perfil.despachante)
    
    if perfil.tipo_usuario != 'ADMIN' and not request.user.is_superuser:
        messages.error(request, "⛔ Permissão Negada.")
        return redirect('gerenciar_servicos')

    # Soft Delete (apenas desativa)
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
    # Otimização com select_related e índices
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
                # Função auxiliar robusta para converter moeda brasileira para Decimal
                def limpar_valor(valor):
                    if not valor: 
                        return Decimal('0.00')
                    try:
                        # Remove pontos de milhar e troca vírgula por ponto
                        v = str(valor).strip().replace('.', '').replace(',', '.')
                        return Decimal(v)
                    except (InvalidOperation, ValueError):
                        return Decimal('0.00')

                # --- 1. CAPTURA DADOS DO FORMULÁRIO ---
                desconto = limpar_valor(request.POST.get('desconto'))
                honorarios_globais = limpar_valor(request.POST.get('honorarios_total'))
                
                cliente_id = request.POST.get('cliente_id')
                nome_avulso = request.POST.get('cliente_nome_avulso')
                observacoes = request.POST.get('observacoes')
                veiculo_id = request.POST.get('veiculo_id')
                
                ids_servicos = request.POST.getlist('servicos[]')
                valores_taxas_lista = request.POST.getlist('taxas_item[]')

                if not ids_servicos:
                    raise Exception("Adicione pelo menos um item ao orçamento.")

                # --- 2. PROCESSA OS ITENS E SOMA AS TAXAS ---
                itens_para_criar = []
                soma_taxas = Decimal('0.00')

                for servico_id, taxa_raw in zip(ids_servicos, valores_taxas_lista):
                    servico_obj = TipoServico.objects.filter(id=servico_id).first()
                    nome_servico = servico_obj.nome if servico_obj else "Serviço Avulso"
                    
                    valor_taxa_item = limpar_valor(taxa_raw)
                    soma_taxas += valor_taxa_item

                    # Preparamos o item para criação posterior
                    itens_para_criar.append(ItemOrcamento(
                        servico_nome=nome_servico,
                        valor=valor_taxa_item
                    ))

                # --- 3. CÁLCULO DE SEGURANÇA NO SERVIDOR ---
                # Total = (Soma das Taxas + Honorário Global) - Desconto
                valor_total_calculado = (soma_taxas + honorarios_globais) - desconto

                # --- 4. CRIAÇÃO DO OBJETO ORÇAMENTO ---
                veiculo_obj = Veiculo.objects.filter(id=veiculo_id).first() if veiculo_id else None
                
                orcamento = Orcamento.objects.create(
                    despachante=perfil.despachante,
                    veiculo=veiculo_obj,
                    observacoes=observacoes,
                    status='PENDENTE',
                    valor_honorarios=honorarios_globais,
                    valor_taxas=soma_taxas,
                    desconto=desconto,
                    valor_total=valor_total_calculado
                )

                # Vincula cliente cadastrado ou nome avulso
                if cliente_id:
                    orcamento.cliente = Cliente.objects.filter(id=cliente_id).first()
                elif nome_avulso:
                    orcamento.nome_cliente_avulso = nome_avulso.upper()
                
                orcamento.save()

                # --- 5. SALVA OS ITENS VINCULADOS ---
                for item in itens_para_criar:
                    item.orcamento = orcamento
                    item.save()

                messages.success(request, f"Orçamento #{orcamento.id} gerado com sucesso!")
                return redirect('detalhe_orcamento', id=orcamento.id)

        except Exception as e:
            print(f"ERRO AO CRIAR ORÇAMENTO: {e}")
            messages.error(request, f"Erro ao processar orçamento: {str(e)}")
            return redirect('novo_orcamento')

    return render(request, 'financeiro/novo_orcamento.html', {'servicos': servicos_disponiveis})

@login_required
def detalhe_orcamento(request, id):
    # Prefetch para otimizar o carregamento dos itens
    orcamento = get_object_or_404(
        Orcamento.objects.prefetch_related('itens'), 
        id=id, 
        despachante=request.user.perfilusuario.despachante
    )
    return render(request, 'financeiro/detalhe_orcamento.html', {'orcamento': orcamento})

@login_required
def aprovar_orcamento(request, id):
    # Garante que o orçamento pertence ao despachante logado
    orcamento = get_object_or_404(Orcamento, id=id, despachante=request.user.perfilusuario.despachante)
    despachante = request.user.perfilusuario.despachante

    if orcamento.status == 'APROVADO':
        messages.warning(request, "Este orçamento já foi aprovado anteriormente.")
        return redirect('listar_orcamentos')

    # Verifica e Cria Cliente Avulso se necessário
    if not orcamento.cliente and orcamento.nome_cliente_avulso:
        try:
            novo_cliente = Cliente.objects.create(
                despachante=orcamento.despachante,
                nome=orcamento.nome_cliente_avulso.upper(),
                telefone="(00) 00000-0000", 
                observacoes="Criado automaticamente via Aprovação de Orçamento."
            )
            orcamento.cliente = novo_cliente
            orcamento.save()
        except Exception as e:
            messages.error(request, f"Erro ao criar cliente avulso: {e}")
            return redirect('detalhe_orcamento', id=id)

    if not orcamento.cliente:
        messages.error(request, "Não foi possível aprovar: Cliente não identificado.")
        return redirect('detalhe_orcamento', id=id)

    try:
        with transaction.atomic():
            # 1. Atualiza status do orçamento
            orcamento.status = 'APROVADO'
            orcamento.save()

            # 2. Pega os valores GLOBAIS que já estão salvos no orçamento (Muito mais fácil!)
            total_taxas_reais = orcamento.valor_taxas
            total_honorarios_reais = orcamento.valor_honorarios
            desconto = orcamento.desconto

            # 3. Monta a lista de serviços para salvar no histórico
            lista_descricoes = []
            detalhes_texto = []
            total_custo_sindego = Decimal('0.00')

            for item in orcamento.itens.all():
                lista_descricoes.append(item.servico_nome)
                detalhes_texto.append(f"- {item.servico_nome}: Taxa R$ {item.valor}")
                
                # --- Cálculo da Taxa Sindicato (SINDEGO) ---
                servico_catalogo = TipoServico.objects.filter(
                    despachante=orcamento.despachante, 
                    nome__iexact=item.servico_nome
                ).first()

                if servico_catalogo:
                    if servico_catalogo.isenta_taxa_sindego:
                        total_custo_sindego += Decimal('0.00')
                    elif servico_catalogo.usa_taxa_sindego_reduzida:
                        total_custo_sindego += despachante.valor_taxa_sindego_reduzida
                    else:
                        total_custo_sindego += despachante.valor_taxa_sindego_padrao
                else:
                    # Se for serviço avulso ou não achar, cobra padrão
                    total_custo_sindego += despachante.valor_taxa_sindego_padrao

            # 4. Cálculo de Lucro Líquido (Honorário - Desconto)
            honorario_liquido = total_honorarios_reais - desconto
            if honorario_liquido < 0: honorario_liquido = Decimal('0.00')

            # 5. Cálculo de Custos Variáveis (Imposto e Banco)
            aliq_imposto = Decimal(str(despachante.aliquota_imposto or 0))
            aliq_banco = Decimal(str(despachante.taxa_bancaria_padrao or 0))

            # Imposto apenas sobre o Honorário Líquido (Nota Fiscal)
            custo_impostos = honorario_liquido * (aliq_imposto / 100)
            
            # Taxa Bancária sobre o valor TOTAL transacionado (Cliente pagou tudo no cartão)
            valor_transacionado = total_taxas_reais + honorario_liquido
            custo_bancario = valor_transacionado * (aliq_banco / 100)

            # 6. Definição de Pagador
            # Se cobramos taxas no orçamento, o dinheiro entrou aqui -> Nós pagamos o Detran
            quem_pagou = 'DESPACHANTE' if total_taxas_reais > 0 else 'CLIENTE'

            # 7. Montagem do texto de observação do processo
            nomes_agrupados = " + ".join(lista_descricoes)[:95] # Limita tamanho do título
            if len(lista_descricoes) > 1:
                nomes_agrupados += "..."

            obs_final = (
                f"Origem: Orçamento #{orcamento.id}.\n"
                f"Itens: {len(lista_descricoes)}\n"
                f"----------------\n" + 
                "\n".join(detalhes_texto) + 
                f"\n\nObs Original: {orcamento.observacoes or ''}"
            )

            # 8. CRIAÇÃO DO PROCESSO (ATENDIMENTO)
            Atendimento.objects.create(
                despachante=orcamento.despachante,
                cliente=orcamento.cliente,
                veiculo=orcamento.veiculo,
                
                tipo_servico=None, # Múltiplos serviços = Null no tipo
                servico=nomes_agrupados, 
                
                # Valores Monetários
                valor_taxas_detran=total_taxas_reais,
                valor_honorarios=honorario_liquido, 
                
                # Custos Calculados
                custo_impostos=custo_impostos,
                custo_taxa_bancaria=custo_bancario,
                custo_taxa_sindego=total_custo_sindego, 
                
                status_financeiro='ABERTO',
                quem_pagou_detran=quem_pagou,
                
                status='SOLICITADO',
                data_solicitacao=timezone.now(), # Agora é DateTimeField
                responsavel=request.user,
                observacoes_internas=obs_final
            )

        messages.success(request, f"Processo gerado com sucesso! Honorário Líquido: R$ {honorario_liquido}")
        return redirect('dashboard')

    except Exception as e:
        print(f"ERRO CRÍTICO AO APROVAR ORÇAMENTO: {e}") 
        messages.error(request, f"Erro crítico ao gerar processo: {str(e)}")
        return redirect('detalhe_orcamento', id=id)

        messages.success(request, f"Processo gerado com sucesso! Honorário Líquido: R$ {honorario_liquido}")
        return redirect('dashboard')

    except Exception as e:
        print(f"ERRO CRÍTICO AO APROVAR ORÇAMENTO: {e}") 
        messages.error(request, f"Erro crítico ao gerar processo: {str(e)}")
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
    
    # Paginação
    paginator = Paginator(orcamentos, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
        
    return render(request, 'financeiro/lista_orcamentos.html', {'orcamentos': page_obj, 'filters': request.GET})

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

    # Otimização de query
    processos = Atendimento.objects.filter(despachante=despachante)\
        .select_related('cliente', 'veiculo', 'responsavel', 'tipo_servico')\
        .order_by('-data_solicitacao')

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
        'processos': processos, # Considere paginar se crescer muito
        'equipe': equipe,
        'resumo_status': resumo_status,
        'total_qtd': processos.count(),
        'filtros': {'data_inicio': data_inicio, 'data_fim': data_fim, 'cliente_placa': cliente_placa, 'responsavel': responsavel_id}
    }
    
    return render(request, 'relatorios/relatorio_mensal.html', context)

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
        # Filtra atendimentos aprovados
        atendimentos = Atendimento.objects.filter(
            despachante=request.user.perfilusuario.despachante,
            status='APROVADO' 
        ).select_related('cliente', 'veiculo').order_by('cliente__nome', '-data_solicitacao')

        # Aplica filtros extras
        if data_inicio: atendimentos = atendimentos.filter(data_solicitacao__gte=data_inicio)
        if data_fim: atendimentos = atendimentos.filter(data_solicitacao__lte=data_fim)
        if status_fin: atendimentos = atendimentos.filter(status_financeiro=status_fin)

        # Filtro de busca textual
        atendimentos = atendimentos.filter(Q(cliente__nome__icontains=cliente_placa) | Q(veiculo__placa__icontains=cliente_placa))

        relatorio_agrupado = {}
        
        for item in atendimentos:
            taxas = item.valor_taxas_detran or 0
            honorarios = item.valor_honorarios or 0
            valor_total_item = taxas + honorarios
            
            placa = item.veiculo.placa if item.veiculo else "S/P"
            modelo = item.veiculo.modelo if item.veiculo else "---"

            cliente_id = item.cliente.id
            
            # Inicializa o grupo do cliente se não existir
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
                    'subtotal_valor': 0,
                    'subtotal_aberto': 0 # <--- NOVO CAMPO: Soma apenas o que falta pagar
                }

            # Adiciona dados do item na lista
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
                'status_fin': item.get_status_financeiro_display(),
                'status_code': item.status_financeiro, # <--- IMPORTANTE: Usado no HTML para filtrar
                'asaas_id': item.asaas_id
            })

            # Formata linha para WhatsApp
            linha_formatada = f"• {item.servico} ({placa}) - R$ {valor_total_item:.2f}"
            relatorio_agrupado[cliente_id]['linhas_zap'].append(linha_formatada)

            # Somas Totais do Cliente
            relatorio_agrupado[cliente_id]['subtotal_taxas'] += taxas
            relatorio_agrupado[cliente_id]['subtotal_honorarios'] += honorarios
            relatorio_agrupado[cliente_id]['subtotal_valor'] += valor_total_item

            # --- TRAVA DE SEGURANÇA ---
            # Só adiciona ao montante "cobrável" se estiver em aberto
            if item.status_financeiro == 'ABERTO':
                relatorio_agrupado[cliente_id]['subtotal_aberto'] += valor_total_item

            # Somas Gerais do Relatório
            total_geral_taxas += taxas
            total_geral_honorarios += honorarios
            total_geral_valor += valor_total_item

        # Monta textos finais do WhatsApp
        for c_id, dados in relatorio_agrupado.items():
            nome_cliente = dados['dados_cliente'].nome.split()[0] if dados['dados_cliente'].nome else "Cliente"
            total_formatado = f"{dados['subtotal_valor']:.2f}"
            lista_servicos = "\n".join(dados['linhas_zap'])
            msg = f"Olá {nome_cliente}, segue o extrato dos seus serviços:\n\n{lista_servicos}\n\n*TOTAL GERAL: R$ {total_formatado}*"
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
@user_passes_test(is_admin_or_superuser, login_url='/dashboard/')
def fluxo_caixa(request):
    despachante = request.user.perfilusuario.despachante
    
    # Filtros da URL
    data_inicio = request.GET.get('data_inicio')
    data_fim = request.GET.get('data_fim')
    cliente_nome = request.GET.get('cliente')
    status_fin = request.GET.get('status_financeiro')

    # QuerySet Base
    processos = Atendimento.objects.filter(
        despachante=despachante, 
        status='APROVADO'
    ).select_related('cliente', 'veiculo').order_by('-data_solicitacao')

    # Aplicação dos Filtros
    if not any([data_inicio, data_fim, cliente_nome, status_fin]):
        hoje = timezone.now().date()
        processos = processos.filter(data_solicitacao__month=hoje.month, data_solicitacao__year=hoje.year)
    else:
        if data_inicio: processos = processos.filter(data_solicitacao__gte=data_inicio)
        if data_fim: processos = processos.filter(data_solicitacao__lte=data_fim)
        if cliente_nome: 
            processos = processos.filter(
                Q(cliente__nome__icontains=cliente_nome) | 
                Q(veiculo__placa__icontains=cliente_nome) |
                Q(numero_atendimento__icontains=cliente_nome)
            )
        if status_fin: processos = processos.filter(status_financeiro=status_fin)

    # --- AGREGAÇÃO DE VALORES ---
    dados_financeiros = processos.aggregate(
        total_taxas=Sum('valor_taxas_detran'),
        total_honorarios=Sum('valor_honorarios'),
        total_impostos=Sum('custo_impostos'),
        total_bancario=Sum('custo_taxa_bancaria'),
        total_sindego=Sum('custo_taxa_sindego') 
    )

    resumo = {
        'total_pendentes': processos.filter(status_financeiro='ABERTO').count(),
        'valor_taxas': dados_financeiros['total_taxas'] or 0,
        'valor_honorarios_bruto': dados_financeiros['total_honorarios'] or 0,
        'valor_impostos': dados_financeiros['total_impostos'] or 0,
        'valor_bancario': dados_financeiros['total_bancario'] or 0,
        'valor_sindego': dados_financeiros['total_sindego'] or 0, 
    }
    
    # --- CÁLCULOS FINAIS ---
    resumo['faturamento_total'] = resumo['valor_taxas'] + resumo['valor_honorarios_bruto']
    
    resumo['total_custos_operacionais'] = (
        resumo['valor_impostos'] + 
        resumo['valor_bancario'] + 
        resumo['valor_sindego']
    )
    
    resumo['lucro_liquido_total'] = resumo['valor_honorarios_bruto'] - resumo['total_custos_operacionais']

    # Paginação para não travar fluxo de caixa
    paginator = Paginator(processos, 50)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'financeiro/fluxo_caixa.html', { 
        'processos': page_obj, 
        'resumo': resumo, 
        'filtros': request.GET
    })

@login_required
def dar_baixa_pagamento(request, id):
    processo = get_object_or_404(Atendimento, id=id, despachante=request.user.perfilusuario.despachante)
    
    processo.status_financeiro = 'PAGO'
    processo.data_pagamento = timezone.now().date()
    processo.save()

    registrar_log(
        request, 
        'FINANCEIRO', 
        f"Confirmou recebimento do processo #{processo.numero_atendimento or id}. Valor Honorários: R$ {processo.valor_honorarios or '0,00'}",
        atendimento=processo,
        cliente=processo.cliente
    )
    
    messages.success(request, f"Recebimento confirmado!")
    return redirect('fluxo_caixa')

@login_required
def gerar_boleto_agrupado(request, cliente_id):
    if request.method != 'POST':
        messages.error(request, "Ação inválida.")
        return redirect('relatorio_servicos')

    despachante = request.user.perfilusuario.despachante
    cliente = get_object_or_404(Cliente, id=cliente_id, despachante=despachante)
    
    # 1. Validação da Chave API
    ASAAS_API_KEY = despachante.asaas_api_key
    if not ASAAS_API_KEY:
        messages.warning(request, "⚠️ Configure sua Chave API do Asaas primeiro.")
        return redirect('configuracoes_despachante')

    # 2. Captura os IDs selecionados
    lista_ids = request.POST.getlist('atendimentos_ids')
    
    atendimentos = Atendimento.objects.filter(
        id__in=lista_ids, 
        despachante=despachante, 
        cliente=cliente,
        status_financeiro='ABERTO'
    ).select_related('veiculo') # Otimiza a busca da placa

    if not atendimentos.exists():
        messages.error(request, "Nenhum atendimento pendente encontrado para gerar boleto.")
        return redirect('relatorio_servicos')

    # 3. Somatório
    valor_total_agrupado = sum(a.valor_total_cliente for a in atendimentos)
    
    # --- 4. MONTAGEM DA DESCRIÇÃO INTELIGENTE (ATUALIZADO) ---
    lista_descricoes = []
    for a in atendimentos:
        placa = a.veiculo.placa if a.veiculo else "S/Placa"
        ref = a.numero_atendimento or str(a.id)
        # Formato: [ABC-1234 / Proc: 1050]
        lista_descricoes.append(f"[{placa} - Proc:{ref}]")

    # Junta tudo com vírgulas
    resumo_veiculos = ", ".join(lista_descricoes)

    # O Asaas tem limite de caracteres, então cortamos se for gigante (aprox 400 chars para sobrar espaço pro aviso)
    if len(resumo_veiculos) > 400:
        resumo_veiculos = resumo_veiculos[:397] + "..."

    descricao_unificada = (
        f"PAGAMENTO REF. {len(atendimentos)} SERVIÇOS DO ESCRITÓRIO {despachante.nome_fantasia.upper()}.\n\n"
        f"VEÍCULOS/PROCESSOS:\n"
        f"{resumo_veiculos}\n\n"
        f"OBS: Este boleto unifica taxas e honorários. "
        f"Para visualizar o detalhamento completo de cada serviço, "
        f"solicite o EXTRATO DE SERVIÇOS ao seu Despachante."
    )
    # ---------------------------------------------------------

    # 5. Integração Asaas
    ASAAS_URL = "https://sandbox.asaas.com/api/v3" 
    
    headers = {
        "Content-Type": "application/json",
        "access_token": ASAAS_API_KEY
    }

    try:
        # 5.1 Busca/Cria Cliente no Asaas
        payload_cliente = {
            "name": cliente.nome,
            "cpfCnpj": cliente.cpf_cnpj,
            "mobilePhone": cliente.telefone,
            "email": cliente.email,
            "postalCode": cliente.cep,
            "address": cliente.rua,
            "addressNumber": cliente.numero,
            "province": cliente.bairro,
            "externalReference": str(cliente.id)
        }

        req_cliente = requests.post(f"{ASAAS_URL}/customers", json=payload_cliente, headers=headers)
        
        if req_cliente.status_code == 200:
            cliente_asaas_id = req_cliente.json().get('id')
        elif "KB001" in req_cliente.text: 
            req_busca = requests.get(f"{ASAAS_URL}/customers?cpfCnpj={cliente.cpf_cnpj}", headers=headers)
            if req_busca.json()['data']:
                cliente_asaas_id = req_busca.json()['data'][0]['id']
            else:
                raise Exception("Erro ao sincronizar cliente Asaas.")
        else:
            req_busca = requests.get(f"{ASAAS_URL}/customers?cpfCnpj={cliente.cpf_cnpj}", headers=headers)
            if req_busca.json()['data']:
                cliente_asaas_id = req_busca.json()['data'][0]['id']
            else:
                raise Exception(f"Erro Asaas Cliente: {req_cliente.text}")

        # 5.2 Gera Cobrança Única com a Nova Descrição
        payload_cobranca = {
            "customer": cliente_asaas_id,
            "billingType": "UNDEFINED",
            "value": float(valor_total_agrupado),
            "dueDate": (timezone.now() + timedelta(days=3)).strftime('%Y-%m-%d'),
            "description": descricao_unificada, # <--- AQUI VAI O TEXTO NOVO
            "externalReference": f"AGRUPADO_{cliente.id}_{timezone.now().timestamp()}"
        }

        req_cobranca = requests.post(f"{ASAAS_URL}/payments", json=payload_cobranca, headers=headers)
        
        if req_cobranca.status_code != 200:
            raise Exception(f"Erro ao criar cobrança: {req_cobranca.text}")

        dados_cobranca = req_cobranca.json()
        boleto_id = dados_cobranca.get('id')
        link_pagamento = dados_cobranca.get('invoiceUrl')

        # 6. Salva o ID do boleto em TODOS os atendimentos
        atendimentos.update(asaas_id=boleto_id)

        messages.success(request, f"Boleto Unificado gerado! Valor: R$ {valor_total_agrupado}")
        return redirect(link_pagamento)

    except Exception as e:
        print(f"ERRO BOLETO: {e}")
        messages.error(request, "Erro na comunicação com Asaas.")
        return redirect('relatorio_servicos')
    
@login_required
@user_passes_test(is_admin_or_superuser, login_url='/dashboard/')
def dashboard_financeiro(request):
    despachante = request.user.perfilusuario.despachante
    
    # --- 1. DEFINIÇÃO DO PERÍODO (FILTROS) ---
    # Se não vier data na URL, pega o mês atual inteiro (do dia 1 até hoje)
    hoje = timezone.now().date()
    inicio_mes = hoje.replace(day=1)
    
    data_inicio = request.GET.get('data_inicio', inicio_mes.strftime('%Y-%m-%d'))
    data_fim = request.GET.get('data_fim', hoje.strftime('%Y-%m-%d'))

    # --- 2. QUERYSET BASE ---
    # Filtra tudo que é APROVADO dentro do período selecionado
    processos = Atendimento.objects.filter(
        despachante=despachante,
        status='APROVADO',
        data_solicitacao__range=[data_inicio, data_fim]
    ).exclude(status='CANCELADO')

    # --- 3. CÁLCULOS NO BANCO DE DADOS (PostgreSQL faz a conta) ---
    zero = Value(0, output_field=DecimalField())

    # Aqui a mágica acontece: O banco soma e subtrai as colunas
    agregados = processos.aggregate(
        # Receita
        soma_taxas=Coalesce(Sum('valor_taxas_detran'), zero),
        soma_honorarios=Coalesce(Sum('valor_honorarios'), zero),
        
        # Custos
        soma_impostos=Coalesce(Sum('custo_impostos'), zero),
        soma_bancario=Coalesce(Sum('custo_taxa_bancaria'), zero),
        soma_sindego=Coalesce(Sum('custo_taxa_sindego'), zero),
        
        # Lucro Líquido calculado no SQL: Honorarios - (Impostos + Banco + Sindicato)
        lucro_liquido_real=Coalesce(Sum(
            F('valor_honorarios') - (
                F('custo_impostos') + 
                F('custo_taxa_bancaria') + 
                F('custo_taxa_sindego')
            )
        ), zero)
    )

    # --- 4. PREPARAÇÃO PARA O DASHBOARD ---
    # Convertendo Decimal para Float para passar pro JSON/Template
    total_taxas = float(agregados['soma_taxas'])
    total_honorarios = float(agregados['soma_honorarios'])
    total_bruto = total_taxas + total_honorarios
    
    custos_ops = float(agregados['soma_impostos'] + agregados['soma_bancario'] + agregados['soma_sindego'])
    lucro_real = float(agregados['lucro_liquido_real'])

    # Dados para o Gráfico de Rosca (Composição)
    pie_data = [
        lucro_real, 
        float(agregados['soma_impostos']), 
        float(agregados['soma_bancario']), 
        float(agregados['soma_sindego'])
    ]

    # --- 5. GRÁFICO DE EVOLUÇÃO (Barras) ---
    # Mostra a evolução mensal DENTRO do período selecionado
    evolucao = processos.annotate(
        mes=ExtractMonth('data_solicitacao')
    ).values('mes').annotate(
        total=Sum('valor_honorarios')
    ).order_by('mes')

    meses_nomes = {
        1: 'Jan', 2: 'Fev', 3: 'Mar', 4: 'Abr', 5: 'Mai', 6: 'Jun',
        7: 'Jul', 8: 'Ago', 9: 'Set', 10: 'Out', 11: 'Nov', 12: 'Dez'
    }

    labels_meses = [meses_nomes.get(item['mes'], 'Mês') for item in evolucao]
    valores_meses = [float(item['total']) for item in evolucao]

    context = {
        'resumo': {
            'bruto': total_bruto,
            'detran': total_taxas,
            'custos_operacionais': custos_ops,
            'lucro': lucro_real,
            'pendente': processos.filter(status_financeiro='ABERTO').count()
        },
        'pie_data': json.dumps(pie_data),
        'labels_meses': json.dumps(labels_meses),
        'valores_meses': json.dumps(valores_meses),
        
        # Devolvemos as datas para manter o input preenchido
        'filtros': {
            'inicio': data_inicio,
            'fim': data_fim
        }
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
            'asaas_id': item.asaas_id,
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
@user_passes_test(is_admin_or_superuser, login_url='/dashboard/')
def relatorio_contabil(request):
    despachante = request.user.perfilusuario.despachante
    
    hoje = timezone.now()
    try:
        mes = int(request.GET.get('mes', hoje.month))
        ano = int(request.GET.get('ano', hoje.year))
    except ValueError:
        mes = hoje.month
        ano = hoje.year
    
    # --- FILTRO ATUALIZADO (SEGURANÇA FISCAL) ---
    processos = Atendimento.objects.filter(
        despachante=despachante,
        data_solicitacao__month=mes,
        data_solicitacao__year=ano,
        status_financeiro='PAGO' # <--- ADICIONADO: Só conta se o dinheiro entrou!
    ).exclude(status__in=['CANCELADO', 'ORCAMENTO']).order_by('data_solicitacao')

    zero = Value(0, output_field=DecimalField())
    
    # 1. Busca os totais do banco de dados
    resumo = processos.aggregate(
        total_honorarios=Coalesce(Sum('valor_honorarios'), zero),
        total_taxas_orgaos=Coalesce(Sum('valor_taxas_detran'), zero),
        total_impostos_retidos=Coalesce(Sum('custo_impostos'), zero),
        # Soma taxas bancárias + sindicato
        total_despesas_operacionais=Coalesce(Sum('custo_taxa_bancaria'), zero) + Coalesce(Sum('custo_taxa_sindego'), zero)
    )

    # 2. Faz os cálculos matemáticos no Python
    total_honorarios = float(resumo['total_honorarios'])
    total_taxas = float(resumo['total_taxas_orgaos'])
    impostos = float(resumo['total_impostos_retidos'])
    despesas = float(resumo['total_despesas_operacionais'])

    # Adiciona os campos calculados para o Template
    resumo['total_movimentado'] = total_honorarios + total_taxas
    resumo['lucro_liquido_estimado'] = total_honorarios - impostos - despesas

    context = {
        'despachante': despachante,
        'processos': processos,
        'resumo': resumo,
        'mes': mes,
        'ano': ano,
        'hoje': hoje,
    }
    
    return render(request, 'relatorios/relatorio_contabil.html', context)

@login_required
def configuracoes_despachante(request):
    despachante = request.user.perfilusuario.despachante

    if request.method == 'POST':
        # Porcentagens
        aliquota_imposto = request.POST.get('aliquota_imposto')
        taxa_bancaria = request.POST.get('taxa_bancaria_padrao')
        
        # Valores Fixos
        taxa_sindego_padrao = request.POST.get('valor_taxa_sindego_padrao')
        taxa_sindego_reduzida = request.POST.get('valor_taxa_sindego_reduzida')
        
        # [NOVO] Honorário Padrão
        honorario_padrao = request.POST.get('valor_honorario_padrao')

        # Chave API
        api_key_asaas = request.POST.get('asaas_api_key')

        # --- PROCESSAMENTO ---
        if aliquota_imposto:
            despachante.aliquota_imposto = aliquota_imposto.replace(',', '.')
            
        if taxa_bancaria:
            despachante.taxa_bancaria_padrao = taxa_bancaria.replace(',', '.')

        if taxa_sindego_padrao:
            despachante.valor_taxa_sindego_padrao = taxa_sindego_padrao.replace('.', '').replace(',', '.')
            
        if taxa_sindego_reduzida:
            despachante.valor_taxa_sindego_reduzida = taxa_sindego_reduzida.replace('.', '').replace(',', '.')

        # [NOVO] Salva o Honorário Padrão
        if honorario_padrao:
            despachante.valor_honorario_padrao = honorario_padrao.replace('.', '').replace(',', '.')

        if api_key_asaas is not None:
            despachante.asaas_api_key = api_key_asaas.strip()

        despachante.save()
        
        messages.success(request, 'Configurações atualizadas com sucesso!')
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


@login_required
@user_passes_test(is_admin_or_superuser, login_url='/dashboard/')
def relatorio_auditoria(request):
    try:
        perfil = request.user.perfilusuario
    except:
        return redirect('dashboard')
    
    # 1. Base: Logs do despachante
    logs = LogAtividade.objects.filter(
        despachante=perfil.despachante
    ).select_related('usuario').order_by('-data')

    # --- CORREÇÃO AQUI: Função para limpar parâmetros sujos ---
    def validar_param(valor):
        if valor in [None, '', 'None', 'Mm', 'dd', 'yyyy']: # Filtra 'None' e lixo comum
            return None
        return valor

    # 2. Captura dos Filtros (Usando a validação)
    data_inicio = validar_param(request.GET.get('data_inicio'))
    data_fim = validar_param(request.GET.get('data_fim'))
    acao = validar_param(request.GET.get('acao'))
    busca = validar_param(request.GET.get('busca'))
    usuario_id = validar_param(request.GET.get('usuario'))

    # --- Aplicação dos Filtros (Só entra se tiver valor válido) ---
    
    if data_inicio:
        logs = logs.filter(data__date__gte=data_inicio)
    
    if data_fim:
        logs = logs.filter(data__date__lte=data_fim)

    if acao:
        logs = logs.filter(acao=acao)
        
    if usuario_id:
        logs = logs.filter(usuario_id=usuario_id)

    if busca:
        logs = logs.filter(
            Q(descricao__icontains=busca) |
            Q(cliente__nome__icontains=busca) |
            Q(usuario__username__icontains=busca) |
            Q(usuario__first_name__icontains=busca)
        )

    # 3. Paginação
    paginator = Paginator(logs, 20) 
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    usuarios_equipe = PerfilUsuario.objects.filter(despachante=perfil.despachante).select_related('user')

    context = {
        'logs': page_obj,
        'usuarios_equipe': usuarios_equipe,
        
        # Passa os valores limpos para o template
        'busca': busca if busca else '',
        'data_inicio': data_inicio if data_inicio else '',
        'data_fim': data_fim if data_fim else '',
        'acao_filtro': acao if acao else '',
        'usuario_filtro': usuario_id if usuario_id else '',
        
        'opcoes_acao': LogAtividade.ACAO_CHOICES,
    }
    
    return render(request, 'relatorios/auditoria.html', context)

# ==============================================================================
# WEBHOOKS (INTEGRAÇÃO ASAAS)
# ==============================================================================

@login_required
def gerar_cobranca_asaas(request, id):
    despachante = request.user.perfilusuario.despachante
    atendimento = get_object_or_404(Atendimento, id=id, despachante=despachante)
    
    # 1. VALIDAÇÃO DE SEGURANÇA
    ASAAS_API_KEY = despachante.asaas_api_key
    
    if not ASAAS_API_KEY:
        messages.warning(request, "⚠️ Para gerar boletos, configure sua Chave de API do Asaas em 'Configurações'.")
        return redirect('configuracoes_despachante')

    # Validações financeiras
    if atendimento.status_financeiro == 'PAGO':
        messages.warning(request, "Este atendimento já consta como pago.")
        return redirect('fluxo_caixa')
        
    valor_cobranca = float(atendimento.valor_total_cliente) # Taxas + Honorários
    
    if valor_cobranca <= 0:
        messages.error(request, "O valor total do atendimento está zerado.")
        return redirect('fluxo_caixa')

    # 2. CONFIGURAÇÃO DA REQUISIÇÃO
    # Use 'https://sandbox.asaas.com/api/v3' para testes
    # Use 'https://www.asaas.com/api/v3' para produção (quando for valer dinheiro)
    ASAAS_URL = "https://sandbox.asaas.com/api/v3" 
    
    headers = {
        "Content-Type": "application/json",
        "access_token": ASAAS_API_KEY
    }

    try:
        # 3. PREPARAÇÃO DOS DADOS DO CLIENTE (COM ENDEREÇO)
        payload_cliente = {
            "name": atendimento.cliente.nome,
            "cpfCnpj": atendimento.cliente.cpf_cnpj,
            "mobilePhone": atendimento.cliente.telefone,
            "email": atendimento.cliente.email,
            
            # --- DADOS DE ENDEREÇO (NOVO) ---
            "postalCode": atendimento.cliente.cep,
            "address": atendimento.cliente.rua,
            "addressNumber": atendimento.cliente.numero,
            "complement": atendimento.cliente.complemento or "",
            "province": atendimento.cliente.bairro, # Asaas chama bairro de 'province'
            "externalReference": str(atendimento.cliente.id)
        }
        
        # Tenta criar o cliente
        req_cliente = requests.post(f"{ASAAS_URL}/customers", json=payload_cliente, headers=headers)
        
        # Lógica de Cliente Existente
        if req_cliente.status_code == 400 and "KB001" in req_cliente.text:
             # Se já existe (erro KB001), buscamos o ID pelo CPF
             req_busca = requests.get(f"{ASAAS_URL}/customers?cpfCnpj={atendimento.cliente.cpf_cnpj}", headers=headers)
             if req_busca.json()['data']:
                 cliente_asaas_id = req_busca.json()['data'][0]['id']
                 
                 # IMPORTANTE: Atualiza o endereço do cliente no Asaas com os dados novos
                 requests.post(f"{ASAAS_URL}/customers/{cliente_asaas_id}", json=payload_cliente, headers=headers)
             else:
                 raise Exception("Erro ao sincronizar cliente no Asaas.")
        elif req_cliente.status_code == 200:
            cliente_asaas_id = req_cliente.json().get('id')
        else:
             # Fallback de busca se der outro erro
             req_busca = requests.get(f"{ASAAS_URL}/customers?cpfCnpj={atendimento.cliente.cpf_cnpj}", headers=headers)
             if req_busca.status_code == 200 and req_busca.json()['data']:
                 cliente_asaas_id = req_busca.json()['data'][0]['id']
             else:
                 raise Exception(f"Erro Asaas Cliente: {req_cliente.text}")

        # 4. GERAÇÃO DA COBRANÇA COM DESCRIÇÃO DETALHADA
        # Monta texto bonito para o boleto
        veiculo_info = "Veículo não informado"
        if atendimento.veiculo:
            veiculo_info = f"{atendimento.veiculo.modelo} - Placa: {atendimento.veiculo.placa}"
            
        descricao_servico = (
            f"REF: Processo {atendimento.numero_atendimento or atendimento.id}\n"
            f"Serviço: {atendimento.servico}\n"
            f"{veiculo_info}"
        )

        payload_cobranca = {
            "customer": cliente_asaas_id,
            "billingType": "UNDEFINED", # Permite Pix ou Boleto
            "value": valor_cobranca,
            "dueDate": (timezone.now() + timedelta(days=3)).strftime('%Y-%m-%d'),
            
            # --- DESCRIÇÃO DETALHADA (NOVO) ---
            "description": descricao_servico,
            
            "externalReference": str(atendimento.id)
        }

        req_cobranca = requests.post(f"{ASAAS_URL}/payments", json=payload_cobranca, headers=headers)
        
        if req_cobranca.status_code != 200:
             raise Exception(f"Erro Asaas Cobrança: {req_cobranca.text}")

        dados_cobranca = req_cobranca.json()
        
        # 5. SALVA E REDIRECIONA
        atendimento.asaas_id = dados_cobranca.get('id')
        atendimento.save()
        
        link_pagamento = dados_cobranca.get('invoiceUrl')
        
        messages.success(request, "Link de pagamento gerado com sucesso!")
        return redirect(link_pagamento) 

    except Exception as e:
        print(f"ERRO INTEGRAÇÃO ASAAS: {e}")
        messages.error(request, "Falha na comunicação com o Asaas. Verifique sua chave de API e conexão.")
        return redirect('fluxo_caixa')
    
def rastreio_publico(request, token):
    # Busca o atendimento pelo Token seguro (UUID)
    atendimento = get_object_or_404(Atendimento, token_rastreio=token)
    
    # Lógica Visual (Progresso e Cores)
    progresso = 0
    cor = 'secondary'
    
    if atendimento.status == 'SOLICITADO':
        progresso = 10
        cor = 'secondary' # Cinza: Apenas recebido
        
    elif atendimento.status == 'EM_ANALISE':
        progresso = 40
        cor = 'info'      # Azul Claro: Estão trabalhando
        
    elif atendimento.status == 'PENDENTE':
        progresso = 40    # Trava no mesmo ponto da análise
        cor = 'warning'   # AMARELO: Alerta visual para o cliente ver a mensagem
        
    elif atendimento.status == 'PROTOCOLADO': # Caso você use futuramente
        progresso = 70
        cor = 'primary'   # Azul Escuro: Já está no Detran
        
    elif atendimento.status == 'APROVADO':
        progresso = 100
        cor = 'success'   # Verde: Sucesso
        
    elif atendimento.status == 'CANCELADO':
        progresso = 100
        cor = 'danger'    # Vermelho: Falha

    context = {
        'atendimento': atendimento,
        'progresso': progresso,
        'cor': cor
    }
    return render(request, 'publico/rastreio.html', context)

# ==============================================================================
# CHATBOT COM IA (MIGRAÇÃO PARA GROQ / LLAMA 3)
# ==============================================================================

# COLE SUA CHAVE 'gsk_...' DENTRO DAS ASPAS ABAIXO
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Inicializa o cliente
client = Groq(api_key=GROQ_API_KEY)

@login_required
@require_POST
def chatbot_responder(request):
    try:
        # 1. Ler a pergunta
        data = json.loads(request.body)
        pergunta_usuario = data.get('pergunta', '').strip()

        if not pergunta_usuario:
            return JsonResponse({'resposta': 'Por favor, digite uma pergunta.'})

        # 2. BUSCA INTELIGENTE (RAG)
        termos = pergunta_usuario.split()
        query = Q()
        for termo in termos:
            if len(termo) > 3: 
                query |= Q(titulo__icontains=termo) | \
                         Q(conteudo__icontains=termo) | \
                         Q(palavras_chave__icontains=termo)
        
        resultados = BaseConhecimento.objects.filter(query, ativo=True).distinct()[:5]

        # 3. MONTAR O CONTEXTO
        contexto_banco = ""
        if resultados.exists():
            contexto_banco = "\n\n".join([f"ASSUNTO: {r.titulo}\nSOLUÇÃO: {r.conteudo}" for r in resultados])
        else:
            contexto_banco = "Nenhuma informação técnica específica encontrada."

        # 4. DEFINIÇÃO DA PERSONALIDADE (Direta e Seca)
        system_prompt = f"""
        Você é a "IA DespachaPro".
        
        --- FONTES TÉCNICAS ---
        {contexto_banco}
        --- FIM FONTES ---

        DIRETRIZES DE ESTILO (IMPORTANTE):
        1. **SEJA DIRETA:** Não use frases de enchimento como "Entendo sua dúvida". Vá direto para a resposta.
        2. **Social:** Se for "Oi", responda curto: "Olá! Em que posso ajudar?".
        3. **Técnico:** Use as FONTES TÉCNICAS acima. Se a resposta estiver lá, entregue apenas o procedimento. 
           - Exemplo BOM: "Faça X. Em seguida, anexe Y."
        4. **Sem Resposta:** Se não tiver no banco, diga apenas: "Não encontrei esse procedimento no manual interno. Favor contatar o suporte."
        
        Responda em Português do Brasil.
        """

        # 5. LÓGICA DE FALLBACK (PLANO A -> PLANO B)
        resposta_texto = ""
        
        try:
            # TENTATIVA 1: O "Einstein" (Modelo 70b - Mais inteligente)
            # Use este como padrão pela qualidade
            chat_completion = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": pergunta_usuario}
                ],
                model="llama-3.3-70b-versatile",
                temperature=0.3,
                max_tokens=500,
            )
            resposta_texto = chat_completion.choices[0].message.content
            
        except Exception as e_principal:
            print(f"⚠️ Erro no modelo principal (70b): {e_principal}")
            print("🔄 Alternando para modelo de backup (8b)...")
            
            # TENTATIVA 2: O "Ligeirinho" (Modelo 8b - Mais rápido, limite 5x maior)
            # Entra em ação se o 70b falhar ou estourar a cota
            try:
                chat_completion = client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": pergunta_usuario}
                    ],
                    model="llama-3.1-8b-instant",
                    temperature=0.3,
                    max_tokens=500,
                )
                resposta_texto = chat_completion.choices[0].message.content
            except Exception as e_backup:
                print(f"❌ Erro total (ambos falharam): {e_backup}")
                return JsonResponse({'resposta': 'Sistema temporariamente indisponível.'}, status=503)

        return JsonResponse({'resposta': resposta_texto})

    except Exception as e:
        print(f"Erro Geral no Chatbot: {e}")
        return JsonResponse({'resposta': 'Erro de comunicação.'}, status=500)
# ==============================================================================
# PAINEL MASTER - BASE DE CONHECIMENTO  
# ==============================================================================
@login_required
@user_passes_test(lambda u: u.is_superuser) # BLOQUEIO TOTAL: Só você entra aqui
def master_listar_conhecimento(request):
    # Busca tudo, ordenado pelos mais recentes
    itens = BaseConhecimento.objects.all().order_by('-data_atualizacao')
    
    # Filtro simples de busca na tela
    busca = request.GET.get('busca')
    if busca:
        itens = itens.filter(titulo__icontains=busca)

    return render(request, 'master/lista_conhecimento.html', {'itens': itens})

@login_required
@user_passes_test(lambda u: u.is_superuser)
def master_editar_conhecimento(request, id=None):
    if id:
        item = get_object_or_404(BaseConhecimento, id=id)
        titulo_pag = "Editar Conhecimento"
    else:
        item = None
        titulo_pag = "Novo Conhecimento"

    if request.method == 'POST':
        form = BaseConhecimentoForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            messages.success(request, "Base de conhecimento atualizada com sucesso!")
            return redirect('master_listar_conhecimento')
    else:
        form = BaseConhecimentoForm(instance=item)

    return render(request, 'master/form_conhecimento.html', {'form': form, 'titulo': titulo_pag})