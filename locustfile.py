from locust import HttpUser, task, between

class UsuarioDoSistema(HttpUser):
    # O tempo que o usuário espera entre um clique e outro (1 a 5 segundos)
    wait_time = between(5, 15)

    def on_start(self):
        """
        Executado quando o robô 'nasce'. 
        Ele precisa pegar o Token de Segurança (CSRF) e fazer Login.
        """
        # 1. Acessa a página de login para pegar o cookie CSRF
        response = self.client.get("/login/")
        if 'csrftoken' in response.cookies:
            csrftoken = response.cookies['csrftoken']
        else:
            # Se falhar, tenta pegar de uma requisição genérica
            print("Aviso: CSRF não encontrado no GET /login/")
            return

        # 2. Faz o POST do login com o token no cabeçalho
        # IMPORTANTE: Troque 'admin' e 'senha123' por um usuário válido do seu banco PostgreSQL
        self.client.post("/login/", 
                         data={"username": "deigopaulo", "password": "123456"}, 
                         headers={"X-CSRFToken": csrftoken})

    @task(3) # Peso 3: Acessa muito o Dashboard
    def ver_dashboard(self):
        self.client.get("/")

    @task(2) # Peso 2: Consulta Clientes
    def listar_clientes(self):
        self.client.get("/clientes/")

    @task(2) # Peso 2: Olha o Fluxo de Caixa (Financeiro)
    def ver_financeiro(self):
        self.client.get("/financeiro/fluxo-caixa/")

    @task(1) # Peso 1: Abre o formulário de novo atendimento (Simula carga)
    def novo_atendimento(self):
        self.client.get("/atendimento/novo/")