from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    # --- Autenticação (Login/Logout) ---
    path('login/', auth_views.LoginView.as_view(template_name='login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),

    # --- RECUPERAÇÃO DE SENHA (NOVAS ROTAS) ---
    # 1. Solicitar a troca (Digitar email)
    path('reset-password/', auth_views.PasswordResetView.as_view(template_name='password_reset.html'), name='password_reset'),
    # 2. Aviso de email enviado
    path('reset-password/done/', auth_views.PasswordResetDoneView.as_view(template_name='password_reset_done.html'), name='password_reset_done'),
    # 3. Link seguro para definir nova senha (uidb64 e token são gerados pelo Django)
    path('reset-password/confirm/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(template_name='password_reset_confirm.html'), name='password_reset_confirm'),
    # 4. Sucesso (Senha alterada)
    path('reset-password/complete/', auth_views.PasswordResetCompleteView.as_view(template_name='password_reset_complete.html'), name='password_reset_complete'),

    # --- Dashboard (Página Inicial) ---
    path('', views.dashboard, name='dashboard'),

    # --- Gestão de Atendimentos (Processos) ---
    path('atendimento/novo/', views.novo_atendimento, name='novo_atendimento'),
    path('atendimento/editar/<int:id>/', views.editar_atendimento, name='editar_atendimento'),
    
    # --- Processo Rápido e APIs ---
    path('novo-processo-rapido/', views.cadastro_rapido, name='cadastro_rapido'),
    path('api/buscar-clientes/', views.buscar_clientes, name='buscar_clientes'),
    path('api/veiculos-cliente/<int:cliente_id>/', views.api_veiculos_cliente, name='api_veiculos_cliente'),
    path('cliente/<int:id>/detalhes/', views.detalhe_cliente, name='detalhe_cliente'),
    path('clientes/', views.lista_clientes, name='lista_clientes'),
    path('relatorios/servicos/', views.relatorio_servicos, name='relatorio_servicos'),
    

    # --- Cadastros de Base ---
    path('cliente/novo/', views.novo_cliente, name='novo_cliente'),
    path('veiculo/novo/', views.novo_veiculo, name='novo_veiculo'),
    path('cliente/editar/<int:id>/', views.editar_cliente, name='editar_cliente'),
    path('veiculo/editar/<int:id>/', views.editar_veiculo, name='editar_veiculo'),

    # --- Exclusão (Admin) ---
    path('cliente/excluir/<int:id>/', views.excluir_cliente, name='excluir_cliente'),
    path('veiculo/excluir/<int:id>/', views.excluir_veiculo, name='excluir_veiculo'),
    path('atendimento/excluir/<int:id>/', views.excluir_atendimento, name='excluir_atendimento'),

    path('servicos/', views.gerenciar_servicos, name='gerenciar_servicos'),
    path('servicos/excluir/<int:id>/', views.excluir_servico, name='excluir_servico'),
    path('orcamento/', views.novo_orcamento, name='novo_orcamento'),
    path('orcamento/<int:id>/', views.detalhe_orcamento, name='detalhe_orcamento'),
    path('orcamento/<int:id>/aprovar/', views.aprovar_orcamento, name='aprovar_orcamento'),
    path('orcamentos/', views.listar_orcamentos, name='listar_orcamentos'),
    path('orcamento/<int:id>/excluir/', views.excluir_orcamento, name='excluir_orcamento'),

    path('documentos/gerar/', views.selecao_documento, name='selecao_documento'),
    path('documentos/imprimir/', views.imprimir_documento, name='imprimir_documento'),
    

    path('relatorios/mensal/', views.relatorio_mensal, name='relatorio_mensal'),
]