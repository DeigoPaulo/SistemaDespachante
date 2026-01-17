from django.shortcuts import redirect, render
from django.urls import reverse
from django.contrib import messages

class BloqueioSaaSMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # 1. Se não estiver logado ou for Superusuário, deixa passar livre
        if not request.user.is_authenticated or request.user.is_superuser:
            return self.get_response(request)

        # 2. Verifica se o usuário tem vínculo com despachante
        if hasattr(request.user, 'perfilusuario') and request.user.perfilusuario.despachante:
            despachante = request.user.perfilusuario.despachante
            perfil = request.user.perfilusuario
            
            # --- LISTA DE URLS PERMITIDAS (Whitelist) ---
            # Estas páginas NUNCA podem ser bloqueadas, senão gera loop infinito.
            rotas_livres = [
                reverse('logout'), 
                reverse('admin:index'), 
                reverse('pagar_mensalidade'),         # Ação de gerar boleto (Botão)
                reverse('bloqueio_financeiro_admin'), # [NOVO] Tela de aviso antes de cobrar
                '/api/webhook/',                      # O Asaas precisa conseguir avisar o pagamento
            ]
            
            # Se a URL atual começa com alguma das livres, libera
            for rota in rotas_livres:
                if request.path.startswith(rota):
                    return self.get_response(request)

            # --- REGRA 1: BLOQUEIO TOTAL (Empresa Desativada no Painel Master) ---
            if not despachante.ativo:
                return render(request, 'financeiro/bloqueio_suspenso.html', {
                    'empresa': despachante.nome_fantasia,
                    'motivo': 'Suspensão Administrativa'
                })

            # --- REGRA 2: BLOQUEIO FINANCEIRO (Data de Validade Expirada) ---
            # Verificamos a data da EMPRESA
            dias_restantes = despachante.get_dias_restantes()
            
            # Se não for vitalício (None) E estiver vencido (< 0)
            if dias_restantes is not None and dias_restantes < 0:
                
                # Se for o DONO (Admin)
                if perfil.tipo_usuario == 'ADMIN':
                    # [MUDANÇA CRÍTICA]
                    # Em vez de mandar pagar direto (gerando boleto), 
                    # manda para a tela de aviso. O boleto só gera se ele clicar no botão lá.
                    if request.path != reverse('bloqueio_financeiro_admin'):
                        return redirect('bloqueio_financeiro_admin')
                    
                    return self.get_response(request)
                
                # Se for FUNCIONÁRIO, mostra tela de bloqueio com aviso
                else:
                    return render(request, 'financeiro/bloqueio_suspenso.html', {
                        'empresa': despachante.nome_fantasia,
                        'motivo': 'Pendência Financeira (Avise seu Administrador)'
                    })

        return self.get_response(request)