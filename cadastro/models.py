from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

# ==============================================================================
# MODELOS EXISTENTES (Mantidos idênticos para compatibilidade)
# ==============================================================================

class Despachante(models.Model):
    nome_fantasia = models.CharField(max_length=255)
    razao_social = models.CharField(max_length=255)
    cnpj = models.CharField(max_length=18, unique=True)
    codigo_sindego = models.CharField(max_length=50, verbose_name="Código SINDEGO")
    telefone = models.CharField(max_length=20)
    email = models.EmailField()
    endereco_completo = models.TextField()
    data_cadastro = models.DateTimeField(auto_now_add=True)
    ativo = models.BooleanField(default=True)

    # --- NOVOS CAMPOS PARA O FINANCEIRO (SaaS / Asaas) ---
    email_fatura = models.EmailField(
        blank=True, null=True, 
        help_text="E-mail que receberá os boletos/Pix da mensalidade."
    )
    valor_mensalidade = models.DecimalField(
        max_digits=10, decimal_places=2, default=100.00, 
        help_text="Valor da assinatura mensal deste despachante."
    )
    
    # ===> NOVO CAMPO: DIA DE VENCIMENTO <===
    # Cria opções do dia 1 ao 28 (para evitar problemas com Fevereiro)
    DIA_VENCIMENTO_CHOICES = [(i, f'Dia {i}') for i in range(1, 29)]
    dia_vencimento = models.IntegerField(
        choices=DIA_VENCIMENTO_CHOICES, 
        default=10, 
        verbose_name="Dia de Vencimento Preferencial"
    )

    asaas_customer_id = models.CharField(
        max_length=50, blank=True, null=True, 
        verbose_name="ID Asaas",
        help_text="ID gerado automaticamente pela integração."
    )

    def __str__(self):
        return self.nome_fantasia


class PerfilUsuario(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    
    # Mantendo sua estrutura original intacta
    despachante = models.ForeignKey(
        'Despachante', on_delete=models.CASCADE, related_name='funcionarios'
    )

    pode_fazer_upload = models.BooleanField(default=False)
    ver_financeiro = models.BooleanField(default=False)

    TIPO_CHOICES = (
        ('ADMIN', 'Administrador'),
        ('OPERAR', 'Operador'),
    )
    tipo_usuario = models.CharField(
        max_length=10, choices=TIPO_CHOICES, default='OPERAR'
    )

    # --- O "TEMPORIZADOR" DE ACESSO ---
    data_expiracao = models.DateField(
        null=True, 
        blank=True, 
        help_text="Data limite para acesso ao sistema. Deixe em branco para acesso vitalício."
    )

    ultimo_session_key = models.CharField(max_length=40, null=True, blank=True)

    def __str__(self):
        return f"{self.user.username} - {self.despachante.nome_fantasia}"

    # Método auxiliar para o Admin mostrar status colorido
    def get_dias_restantes(self):
        if not self.data_expiracao:
            return None # Sem limite
        hoje = timezone.now().date()
        return (self.data_expiracao - hoje).days


class Cliente(models.Model):
    despachante = models.ForeignKey(Despachante, on_delete=models.CASCADE)

    nome = models.CharField(max_length=255, db_index=True)
    cpf_cnpj = models.CharField(max_length=18, db_index=True)

    rg = models.CharField(max_length=20, blank=True, null=True)
    orgao_expedidor = models.CharField(max_length=20, blank=True, null=True)
    uf_rg = models.CharField(max_length=2, blank=True, null=True, verbose_name="UF do RG") # NOVO
    naturalidade = models.CharField(max_length=100, blank=True, null=True)
    filiacao = models.CharField(max_length=200, blank=True, null=True, verbose_name="Filiação (Mãe/Pai)")
    estado_civil = models.CharField(max_length=50, blank=True, null=True)
    profissao = models.CharField(max_length=100, blank=True, null=True)

    rua = models.CharField(max_length=255)
    numero = models.CharField(max_length=50)
    quadra = models.CharField(max_length=50, blank=True, null=True)
    lote = models.CharField(max_length=50, blank=True, null=True)
    complemento = models.CharField(max_length=255, blank=True, null=True)
    bairro = models.CharField(max_length=100)
    cidade = models.CharField(max_length=100, default="Goiânia")
    uf = models.CharField(max_length=2, default="GO")
    cep = models.CharField(max_length=10)

    telefone = models.CharField(max_length=20)
    email = models.EmailField(blank=True, null=True)
    
    

    def __str__(self):
        return self.nome


class Veiculo(models.Model):
    despachante = models.ForeignKey(Despachante, on_delete=models.CASCADE)
    cliente = models.ForeignKey(
        Cliente, on_delete=models.CASCADE, related_name='veiculos'
    )

    placa = models.CharField(max_length=7)
    renavam = models.CharField(max_length=11, blank=True, null=True)
    chassi = models.CharField(max_length=17, blank=True, null=True)
    marca = models.CharField(max_length=50)
    modelo = models.CharField(max_length=100)
    cor = models.CharField(max_length=30)
    ano_fabricacao = models.PositiveIntegerField()
    ano_modelo = models.PositiveIntegerField()

    TIPO_VEICULO_CHOICES = (
        ('CARRO', 'Carro'),
        ('MOTO', 'Moto'),
        ('CAMINHAO', 'Caminhão'),
        ('REBOQUE', 'Reboque'),
    )
    tipo = models.CharField(max_length=20, choices=TIPO_VEICULO_CHOICES)

    class Meta:
        unique_together = ('despachante', 'placa')

    def __str__(self):
        return f"{self.placa} - {self.modelo}"


class Atendimento(models.Model):
    STATUS_CHOICES = (
        ('SOLICITADO', 'Solicitado'),
        ('EM_ANALISE', 'Em Análise'),
        ('APROVADO', 'Aprovado'),
        ('CANCELADO', 'Cancelado'),
    )

    despachante = models.ForeignKey('Despachante', on_delete=models.CASCADE)
    cliente = models.ForeignKey('Cliente', on_delete=models.CASCADE)
    veiculo = models.ForeignKey('Veiculo', on_delete=models.CASCADE)

    # --- NOVO CAMPO: Responsável Técnico ---
    # Armazena quem é o responsável pelo processo (pode ser diferente de quem digitou)
    responsavel = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='atendimentos_responsaveis',
        verbose_name="Responsável Técnico"
    )

    numero_atendimento = models.CharField(max_length=50, blank=True, null=True)
    servico = models.CharField(max_length=100) 

    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='SOLICITADO'
    )

    observacoes_internas = models.TextField(blank=True, null=True)
    
    data_solicitacao = models.DateField(default=timezone.now)
    data_entrega = models.DateField(null=True, blank=True, verbose_name="Prazo de Entrega")

    def __str__(self):
        return f"{self.numero_atendimento} - {self.cliente}"

# ==============================================================================
# NOVOS MODELOS (Gestão de Serviços e Orçamentos)
# ==============================================================================

class TipoServico(models.Model):
    """
    Tabela de preços configurável por cada Despachante.
    Ex: Transferência, 2ª Via, Primeiro Emplacamento.
    """
    despachante = models.ForeignKey(Despachante, on_delete=models.CASCADE)
    nome = models.CharField(max_length=100)  
    
    # Valores financeiros (Decimal é melhor que Float para dinheiro)
    valor_base = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Custo DETRAN") 
    honorarios = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Lucro/Honorários") 
    
    ativo = models.BooleanField(default=True)

    def __str__(self):
        return self.nome

    @property
    def valor_total(self):
        return self.valor_base + self.honorarios


class Orcamento(models.Model):
    """
    Cabeçalho do Orçamento (Cliente, Total, Data)
    """
    STATUS_ORCAMENTO = (
        ('PENDENTE', 'Pendente'),
        ('APROVADO', 'Aprovado (Gerou Processo)'),
        ('CANCELADO', 'Cancelado/Recusado'),
    )

    despachante = models.ForeignKey(Despachante, on_delete=models.CASCADE)
    
    # --- MUDANÇA 1: Cliente agora é opcional (null=True) e usamos SET_NULL ---
    # Motivo: Se você excluir o cliente do cadastro, não queremos apagar o histórico de orçamentos dele.
    cliente = models.ForeignKey(Cliente, on_delete=models.SET_NULL, null=True, blank=True)
    
    # --- MUDANÇA 2: Novo campo para armazenar o nome de quem não tem cadastro ---
    nome_cliente_avulso = models.CharField(max_length=200, blank=True, null=True)

    data_criacao = models.DateTimeField(auto_now_add=True)
    validade = models.DateField(null=True, blank=True)
    
    observacoes = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_ORCAMENTO, default='PENDENTE')
    
    # Campos de totais
    valor_total = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    desconto = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    def __str__(self):
        nome = self.cliente.nome if self.cliente else self.nome_cliente_avulso
        return f"Orçamento #{self.id} - {nome}"

    @property
    def valor_final(self):
        return self.valor_total - self.desconto
    
    @property
    def nome_cliente_display(self):
        """Retorna o nome do cliente (seja do banco ou avulso)"""
        if self.cliente:
            return self.cliente.nome
        return self.nome_cliente_avulso or "Cliente Desconhecido"

# --- MUDANÇA 3: Modelo para os Itens do Orçamento ---
class ItemOrcamento(models.Model):
    orcamento = models.ForeignKey(Orcamento, related_name='itens', on_delete=models.CASCADE)
    servico_nome = models.CharField(max_length=200) # Salvamos o texto, pois o preço na tabela pode mudar depois
    valor = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.servico_nome} - R$ {self.valor}"
    

