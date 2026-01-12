from django.urls import path
from django.contrib.auth import views as auth_views
from . import views 

urlpatterns = [
    # ==========================================================================
    # 1. AUTENTICAÇÃO E CONTA
    # ==========================================================================
    path('login/', views.minha_view_de_login, name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),

    # Recuperação de Senha
    path('reset-password/', auth_views.PasswordResetView.as_view(template_name='registration/password_reset_form.html'), name='password_reset'),
    path('reset-password/done/', auth_views.PasswordResetDoneView.as_view(template_name='registration/password_reset_done.html'), name='password_reset_done'),
    path('reset-password/confirm/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(template_name='registration/password_reset_confirm.html'), name='password_reset_confirm'),
    path('reset-password/complete/', auth_views.PasswordResetCompleteView.as_view(template_name='registration/password_reset_complete.html'), name='password_reset_complete'),

    # ==========================================================================
    # 2. DASHBOARD E OPERACIONAL
    # ==========================================================================
    path('', views.dashboard, name='dashboard'),
    
    # Processos / Atendimentos
    path('atendimento/novo/', views.novo_atendimento, name='novo_atendimento'),
    path('atendimento/editar/<int:id>/', views.editar_atendimento, name='editar_atendimento'),
    path('atendimento/excluir/<int:id>/', views.excluir_atendimento, name='excluir_atendimento'),
    path('novo-processo-rapido/', views.cadastro_rapido, name='cadastro_rapido'),
    
    # Configurações e Utilitários de Impressão
    path('configuracoes/', views.configuracoes_despachante, name='configuracoes_despachante'),
    path('recibo/<int:id>/', views.emitir_recibo, name='emitir_recibo'),

    # ==========================================================================
    # 3. CADASTROS DE BASE (Clientes, Veículos, Serviços)
    # ==========================================================================
    path('clientes/', views.lista_clientes, name='lista_clientes'),
    path('cliente/novo/', views.novo_cliente, name='novo_cliente'),
    path('cliente/editar/<int:id>/', views.editar_cliente, name='editar_cliente'),
    path('cliente/excluir/<int:id>/', views.excluir_cliente, name='excluir_cliente'),
    path('cliente/<int:id>/detalhes/', views.detalhe_cliente, name='detalhe_cliente'),

    path('veiculo/novo/', views.novo_veiculo, name='novo_veiculo'),
    path('veiculo/editar/<int:id>/', views.editar_veiculo, name='editar_veiculo'),
    path('veiculo/excluir/<int:id>/', views.excluir_veiculo, name='excluir_veiculo'),

    path('servicos/', views.gerenciar_servicos, name='gerenciar_servicos'),
    path('servicos/excluir/<int:id>/', views.excluir_servico, name='excluir_servico'),
    path('servicos/editar/<int:id>/', views.editar_servico, name='editar_servico'),

    # ==========================================================================
    # 4. ORÇAMENTOS E COMERCIAL
    # ==========================================================================
    path('orcamentos/', views.listar_orcamentos, name='listar_orcamentos'),
    path('orcamento/', views.novo_orcamento, name='novo_orcamento'),
    path('orcamento/<int:id>/', views.detalhe_orcamento, name='detalhe_orcamento'),
    path('orcamento/<int:id>/aprovar/', views.aprovar_orcamento, name='aprovar_orcamento'),
    path('orcamento/<int:id>/excluir/', views.excluir_orcamento, name='excluir_orcamento'),

    # ==========================================================================
    # 5. FERRAMENTAS E UTILITÁRIOS
    # ==========================================================================
    path('api/buscar-clientes/', views.buscar_clientes, name='buscar_clientes'),
    path('api/veiculos-cliente/<int:cliente_id>/', views.api_veiculos_cliente, name='api_veiculos_cliente'),
    
    path('documentos/gerar/', views.selecao_documento, name='selecao_documento'),
    path('documentos/imprimir/', views.imprimir_documento, name='imprimir_documento'),
    path('ferramentas/comprimir-pdf/', views.ferramentas_compressao, name='ferramentas_compressao'),

    path('relatorios/servicos/', views.relatorio_servicos, name='relatorio_servicos'),
    path('relatorios/mensal/', views.relatorio_mensal, name='relatorio_mensal'),
    
    # Logs de Auditoria
    path('configuracoes/auditoria/', views.relatorio_auditoria, name='relatorio_auditoria'),

    # Financeiro (Pagamento da mensalidade do software)
    path('financeiro/pagar/', views.pagar_mensalidade, name='pagar_mensalidade'),

    # ==========================================================================
    # 6. MÓDULO FINANCEIRO (Gestão do Escritório)
    # ==========================================================================
    path('financeiro/dashboard/', views.dashboard_financeiro, name='dashboard_financeiro'),
    path('financeiro/fluxo-caixa/', views.fluxo_caixa, name='fluxo_caixa'),
    
    # --- ROTA DO RELATÓRIO CONTÁBIL ---
    path('financeiro/relatorio-contabil/', views.relatorio_contabil, name='relatorio_contabil'),
    # ----------------------------------

    path('financeiro/baixa/<int:id>/', views.dar_baixa_pagamento, name='dar_baixa_pagamento'),
    path('financeiro/inadimplencia/', views.relatorio_inadimplencia, name='relatorio_inadimplencia'),

    # ==========================================================================
    # 7. ÁREA MASTER (Admin do SaaS)
    # ==========================================================================
    path('financeiro/master/', views.financeiro_master, name='financeiro_master'),
    path('financeiro/cobrar/<int:despachante_id>/', views.acao_cobrar_cliente, name='acao_cobrar_cliente'),
    path('financeiro/liberar/<int:despachante_id>/', views.acao_liberar_acesso, name='acao_liberar_acesso'),

    # Gestão de Despachantes (Empresas)
    path('master/despachantes/', views.master_listar_despachantes, name='master_listar_despachantes'),
    path('master/despachantes/novo/', views.master_editar_despachante, name='master_criar_despachante'),
    path('master/despachantes/<int:id>/', views.master_editar_despachante, name='master_editar_despachante'),

    # Gestão de Usuários (Equipe)
    path('master/usuarios/', views.master_listar_usuarios, name='master_listar_usuarios'),
    path('master/usuarios/novo/', views.master_criar_usuario, name='master_criar_usuario'),
    path('master/usuarios/<int:id>/', views.master_editar_usuario, name='master_editar_usuario'),
]