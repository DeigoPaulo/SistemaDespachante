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

# ==============================================================================
# 1. VIEW DE LOGIN (Sua lógica original mantida)
# ==============================================================================
def minha_view_de_login(request):
    contexto = {'erro_login': False}

    if request.method == 'POST':
        # 1. Pega dados do form
        username_form = request.POST.get('username')
        password_form = request.POST.get('password')

        # 2. Autentica
        user = authenticate(request, username=username_form, password=password_form)

        if user is not None:
            # Faz o login (cria sessão em memória)
            login(request, user)

            # GARANTIA: Se a sessão não tiver chave ainda, força criar e salvar agora
            if not request.session.session_key:
                request.session.create()

            nova_chave = request.session.session_key

            # 3. Lógica de Single Session (Um dispositivo por vez)
            # Usa get_or_create para evitar erro se o perfil ainda não existir
            perfil, created = PerfilUsuario.objects.get_or_create(user=user)
            chave_antiga = perfil.ultimo_session_key

            if chave_antiga and chave_antiga != nova_chave:
                try:
                    # Tenta apagar a sessão anterior do banco
                    Session.objects.get(session_key=chave_antiga).delete()
                except Session.DoesNotExist:
                    # Se já não existia, segue o jogo
                    pass

            # 4. Atualiza o perfil com a chave atual
            perfil.ultimo_session_key = nova_chave
            perfil.save()

            return redirect('dashboard')
        
        else:
            # Senha incorreta
            contexto['erro_login'] = True

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


@login_required
def editar_atendimento(request, id):
    """
    Permite alterar status, PRAZOS e observações.
    """
    perfil = request.user.perfilusuario
    
    # Busca o atendimento garantindo que pertence ao despachante logado
    atendimento = get_object_or_404(Atendimento, id=id, despachante=perfil.despachante)
    
    if request.method == 'POST':
        form = AtendimentoForm(request.user, request.POST, instance=atendimento)
        if form.is_valid():
            form.save()
            # Redireciona de volta para o Dashboard para ver a mudança
            return redirect('dashboard')
    else:
        form = AtendimentoForm(request.user, instance=atendimento)
        
    return render(request, 'form_generico.html', {
        'form': form, 
        'titulo': f'Editar Processo #{atendimento.numero_atendimento or "S/N"}'
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
    
    return render(request, 'detalhe_cliente.html', {
        'cliente': cliente,
        'veiculos': veiculos
    })

# --- FUNÇÕES DE EXCLUSÃO (SOMENTE ADMIN) ---

@login_required
def excluir_cliente(request, id):
    # 1. Identifica o usuário logado
    try:
        perfil = request.user.perfilusuario
    except:
        return redirect('dashboard') # Segurança extra se o user estiver bugado

    # 2. Busca Segura: Só acha se pertencer ao despachante logado
    cliente = get_object_or_404(Cliente, id=id, despachante=perfil.despachante)

    # 3. Só deleta se for uma requisição POST (vinda do Modal/Formulário)
    if request.method == 'POST':
        nome = cliente.nome
        cliente.delete()
        messages.success(request, f"Cliente '{nome}' excluído com sucesso.")
        return redirect('lista_clientes')
    
    # Se tentar acessar via URL direta (GET), apenas redireciona sem apagar
    return redirect('lista_clientes')

@login_required
def lista_clientes(request):
    perfil = request.user.perfilusuario
    clientes = Cliente.objects.filter(despachante=perfil.despachante).order_by('nome')
    return render(request, 'lista_clientes.html', {'clientes': clientes})

@login_required
def excluir_veiculo(request, id):
    perfil = request.user.perfilusuario
    veiculo = get_object_or_404(Veiculo, id=id, despachante=perfil.despachante)

    if request.method == 'POST':
        placa = veiculo.placa
        veiculo.delete()
        messages.success(request, f"Veículo {placa} excluído.")
        return redirect('lista_clientes') # Geralmente voltamos pra lista de clientes ou dashboard

    return redirect('lista_clientes')

@login_required
def excluir_atendimento(request, id):
    perfil = request.user.perfilusuario
    atendimento = get_object_or_404(Atendimento, id=id, despachante=perfil.despachante)

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
    return render(request, 'cadastro_cliente.html')

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
    Agora carrega os serviços cadastrados no banco para o select.
    """
    perfil = getattr(request.user, 'perfilusuario', None)
    if not perfil:
        return redirect('logout')

    # --- NOVO: Carrega serviços para o dropdown dinâmico ---
    servicos_db = TipoServico.objects.filter(despachante=perfil.despachante, ativo=True)

    if request.method == 'POST':
        try:
            with transaction.atomic():
                despachante = perfil.despachante

                # 1. VERIFICA SE UM CLIENTE FOI SELECIONADO
                cliente_id = request.POST.get('cliente_id')
                if not cliente_id:
                    print("❌ Erro: Nenhum cliente selecionado.")
                    return redirect('cadastro_rapido')
                
                cliente = get_object_or_404(Cliente, id=cliente_id, despachante=despachante)

                # 2. CAPTURA AS LISTAS DE DADOS
                # Arrays vindos do formulário dinâmico
                placas = request.POST.getlist('veiculo_placa[]')
                renavams = request.POST.getlist('veiculo_renavam[]')
                modelos = request.POST.getlist('veiculo_modelo[]')
                cores = request.POST.getlist('veiculo_cor[]')
                anos = request.POST.getlist('veiculo_ano[]') # Usamos um campo só para ano no modal
                marcas = request.POST.getlist('veiculo_marca[]')
                chassis = request.POST.getlist('veiculo_chassi[]')
                tipos = request.POST.getlist('veiculo_tipo[]')
                
                servicos = request.POST.getlist('servico[]')
                atendimentos = request.POST.getlist('numero_atendimento[]')
                obs_geral = request.POST.get('observacoes', '')
                
                # --- NOVO: Captura a data de entrega manual ---
                prazo_input = request.POST.get('prazo_entrega')

                # 3. LOOP PARA SALVAR VEÍCULOS E CRIAR PROCESSOS
                for i in range(len(placas)):
                    # Limpeza básica da placa
                    placa_limpa = placas[i].replace('-', '').replace(' ', '').upper()
                    if not placa_limpa: continue
                    if len(placa_limpa) > 7: placa_limpa = placa_limpa[:7]

                    # Tratamento de ano (evita erro se vier vazio)
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
                        numero_atendimento=num_atend_atual,
                        observacoes_internas=obs_geral,
                        # --- Salva a data manual se ela existir ---
                        data_entrega=prazo_input if prazo_input else None,
                        status='SOLICITADO'
                    )

            return redirect('dashboard')

        except Exception as e:
            print(f"❌ Erro Crítico no Cadastro Rápido: {e}")
            pass

    # Renderiza o template passando os serviços do banco
    return render(request, 'cadastro_rapido.html', {
        'servicos_db': servicos_db
    })

# --- API DE BUSCA DE CLIENTES (AUTOCOMPLETE) ---


@login_required
def buscar_clientes(request):
    term = request.GET.get('term', '')
    
    # Garante que o usuário tem perfil
    perfil = getattr(request.user, 'perfilusuario', None)
    if not perfil:
        return JsonResponse({'results': []}, safe=False)

    despachante = perfil.despachante
    
    # 1. Limpa o termo para tentar buscar apenas por números (CPF limpo)
    term_limpo = re.sub(r'\D', '', term) 

    # 2. Monta a query PODEROSA:
    # - Nome contém o termo OU
    # - CPF contém o termo exato digitado OU
    # - CPF contém apenas os números digitados OU
    # - PLACA de algum veículo do cliente contém o termo (NOVO!)
    filters = Q(despachante=despachante) & (
        Q(nome__icontains=term) | 
        Q(cpf_cnpj__icontains=term) | 
        Q(cpf_cnpj__icontains=term_limpo) |
        Q(veiculos__placa__icontains=term) # <--- A MÁGICA ACONTECE AQUI
    )

    # .distinct() é obrigatório aqui porque estamos filtrando por uma tabela relacionada (veículos)
    clientes = Cliente.objects.filter(filters).distinct()[:20]

    results = []
    for c in clientes:
        # Montamos o texto que vai aparecer na lista
        text_display = f"{c.nome} | CPF: {c.cpf_cnpj}"
        
        results.append({
            'id': c.id,
            'text': text_display,  # O Select2 usa o campo 'text' para exibir
            'cpf': c.cpf_cnpj,
            'telefone': c.telefone
        })
    
    # Retornamos num formato padrão { "results": [...] }
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
    return render(request, 'editar_cliente.html', {
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
    return render(request, 'editar_veiculo.html', {
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
    perfil = request.user.perfilusuario
    servico = get_object_or_404(TipoServico, id=id, despachante=perfil.despachante)
    servico.ativo = False # Soft delete para não quebrar histórico
    servico.save()
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

    return render(request, 'novo_orcamento.html', {'servicos': servicos_disponiveis})

# views.py

@login_required
def detalhe_orcamento(request, id):
    # Busca o orçamento garantindo que pertence ao despachante logado
    orcamento = get_object_or_404(Orcamento, id=id, despachante=request.user.perfilusuario.despachante)
    
    return render(request, 'detalhe_orcamento.html', {'orcamento': orcamento})

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
        
    return render(request, 'lista_orcamentos.html', {
        'orcamentos': orcamentos,
        'filters': request.GET 
    })

@login_required
def excluir_orcamento(request, id):
    # 1. Busca Segura (Só acha se for do despachante logado)
    orcamento = get_object_or_404(Orcamento, id=id, despachante=request.user.perfilusuario.despachante)
    
    # 2. Executa a exclusão apenas se for POST
    if request.method == 'POST':
        orcamento.delete()
        messages.success(request, f"Orçamento #{id} excluído com sucesso.")
        return redirect('listar_orcamentos')
    
    # Se tentar acessar direto pela URL, volta para a lista
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

    return render(request, 'relatorio_mensal.html', context)


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

    return render(request, 'relatorio_servicos.html', context)

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
        
        # Dados para lógica do Outorgado (Procuração Particular)
        tipo_outorgado = request.POST.get('tipo_outorgado') # 'escritorio' ou 'outro'
        outorgado_id = request.POST.get('outorgado_id')

        # 2. Pega o Despachante Logado (usaremos várias vezes)
        despachante_obj = request.user.perfilusuario.despachante

        # 3. BUSCA SEGURA DO CLIENTE (OUTORGANTE)
        cliente = get_object_or_404(
            Cliente, 
            id=cliente_id, 
            despachante=despachante_obj
        )
        
        # 4. BUSCA DO VEÍCULO (Opcional)
        veiculo = None
        if veiculo_placa:
            veiculo = Veiculo.objects.filter(placa=veiculo_placa, cliente=cliente).first()

        # 5. LÓGICA DO OUTORGADO (Quem recebe os poderes)
        # Cria um dicionário padrão para facilitar o uso no template
        outorgado_dados = {}

        if tipo_doc == 'procuracao_particular' and tipo_outorgado == 'outro' and outorgado_id:
            # Se escolheu "Outra Pessoa", buscamos ela no banco de Clientes
            try:
                pessoa = Cliente.objects.get(id=outorgado_id, despachante=despachante_obj)
                outorgado_dados = {
                    'nome': pessoa.nome.upper(),
                    'doc': f"CPF/CNPJ: {pessoa.cpf_cnpj}",
                    'rg': f"RG: {pessoa.rg or ''} {pessoa.orgao_expedidor or ''}",
                    'endereco': f"{pessoa.rua}, {pessoa.numero}, {pessoa.bairro}",
                    'cidade': f"{pessoa.cidade}/{pessoa.uf}",
                    'cep': pessoa.cep,
                    'telefone': pessoa.telefone
                }
            except Cliente.DoesNotExist:
                # Fallback de segurança: se não achar, usa o escritório
                outorgado_dados = _dados_do_escritorio(despachante_obj)
        else:
            # Padrão: O Outorgado é o Escritório/Despachante
            outorgado_dados = _dados_do_escritorio(despachante_obj)

        # 6. FORMATAÇÃO DOS SERVIÇOS
        lista_nomes_servicos = []
        if servicos_selecionados_ids:
            servicos_objs = TipoServico.objects.filter(id__in=servicos_selecionados_ids)
            lista_nomes_servicos = [s.nome for s in servicos_objs]
        
        texto_servicos = ", ".join(lista_nomes_servicos) if lista_nomes_servicos else "______________________________________________________"

        # 7. CONTEXTO GERAL (Enviado para o PDF)
        context = {
            'cliente': cliente,
            'veiculo': veiculo,
            'despachante': despachante_obj,
            'outorgado': outorgado_dados, # <--- NOVO: Dados prontos do procurador
            'hoje': timezone.now(),
            'servicos_solicitados': texto_servicos,
            'motivo_2via': motivo_2via,
            'alteracao_pretendida': alteracao_pretendida,
            'valor_recibo': valor_recibo
        }

        # 8. SELEÇÃO DO DOCUMENTO
        if tipo_doc == 'procuracao':
            return render(request, 'documentos/print_procuracao.html', context)
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
    
        
            
    return redirect('selecao_documento')

# Função auxiliar interna para não repetir código (coloque fora da view ou no mesmo arquivo)
def _dados_do_escritorio(despachante):
    return {
        'nome': despachante.nome_fantasia.upper(),
        'doc': f"CNPJ: {despachante.cnpj} | Credencial: {despachante.codigo_sindego}",
        'rg': "", 
        'endereco': despachante.endereco_completo,
        'cidade': "Goiânia/GO", # Ou despachante.cidade_uf se tiver
        'cep': "",
        'telefone': despachante.telefone
    }