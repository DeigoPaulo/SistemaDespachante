from django.shortcuts import redirect
from django.contrib import messages
from functools import wraps

def plano_minimo(plano_exigido):
    """
    Decorator que verifica se o Despachante tem o plano mínimo necessário.
    Hierarquia: BASICO (1) < MEDIO (2) < PREMIUM (3)
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            # 1. Superusuário (Master) acessa tudo sem restrição
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)

            # 2. Verifica se está autenticado (segurança extra)
            if not request.user.is_authenticated:
                return redirect('login')

            # 3. Tenta recuperar o plano do despachante vinculado ao usuário
            try:
                # Caminho: User -> PerfilUsuario -> Despachante -> Campo 'plano'
                plano_atual = request.user.perfilusuario.despachante.plano
            except AttributeError:
                # Se o usuário não tiver perfil ou despachante vinculado, manda pro login
                return redirect('login')

            # 4. Define a "força" (peso) de cada plano para comparação matemática
            niveis = {
                'BASICO': 1,
                'MEDIO': 2,
                'PREMIUM': 3
            }

            # 5. Mapeia os códigos para nomes amigáveis (para a mensagem de erro)
            nomes_amigaveis = {
                'BASICO': 'Básico',
                'MEDIO': 'Médio',
                'PREMIUM': 'Premium'
            }

            # Converte os planos em números. Se não achar, assume 1 (Básico)
            nivel_usuario = niveis.get(plano_atual, 1)
            nivel_necessario = niveis.get(plano_exigido, 1)

            # 6. A Lógica de Bloqueio
            if nivel_usuario >= nivel_necessario:
                # Se o nível do usuário for maior ou igual ao exigido, deixa passar
                return view_func(request, *args, **kwargs)
            else:
                # Bloqueia e avisa
                nome_plano = nomes_amigaveis.get(plano_exigido, plano_exigido)
                messages.warning(
                    request, 
                    f"🔒 Acesso Restrito: Funcionalidade exclusiva do Plano {nome_plano}. Faça um upgrade!"
                )
                return redirect('dashboard')

        return _wrapped_view
    return decorator