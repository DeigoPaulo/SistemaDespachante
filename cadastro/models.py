from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.validators import FileExtensionValidator # <--- Importação necessária para validar o PNG

# ==============================================================================
# MODELOS EXISTENTES
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

    # --- PERSONALIZAÇÃO VISUAL (NOVO) ---
    logo = models.ImageField(
        upload_to='logos_despachantes/', 
        null=True, 
        blank=True,
        verbose_name="Logo do Escritório",
        help_text="Formato obrigatório: PNG com fundo transparente. Tamanho ideal: 300x100px.",
        validators=[FileExtensionValidator(['png'])] # <--- Trava para aceitar só PNG
    )

    # --- CONFIGURAÇÕES DE CUSTOS PADRÃO (SaaS) ---
    # Para não precisar digitar toda vez, o sistema calcula automático baseado nisso:
    aliquota_imposto = models.DecimalField(
        max_digits=5, decimal_places=2, default=0.00, 
        help_text="Alíquota de imposto (ex: 5.00 para 5%)"
    )
    taxa_bancaria_padrao = models.DecimalField(
        max_digits=5, decimal_places=2, default=0.00, 
        help_text="Taxa bancária/maquininha (ex: 2.50 para 2.5%)"
    )

    # --- NOVOS CAMPOS PARA O FINANCEIRO (SaaS / Asaas) ---
    email_fatura = models.EmailField(
        blank=True, null=True,
        help_text="E-mail que receberá os boletos/Pix da mensalidade."
    )
    valor_mensalidade = models.DecimalField(
        max_digits=10, decimal_places=2, default=100.00,
        help_text="Valor da assinatura mensal deste despachante."
    )
    
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

    data_expiracao = models.DateField(
        null=True,
        blank=True,
        help_text="Data limite para acesso ao sistema. Deixe em branco para acesso vitalício."
    )

    ultimo_session_key = models.CharField(max_length=40, null=True, blank=True)

    def __str__(self):
        return f"{self.user.username} - {self.despachante.nome_fantasia}"

    def get_dias_restantes(self):
        if not self.data_expiracao:
            return None
        hoje = timezone.now().date()
        return (self.data_expiracao - hoje).days


class Cliente(models.Model):
    despachante = models.ForeignKey(Despachante, on_delete=models.CASCADE)
    nome = models.CharField(max_length=255, db_index=True)
    cpf_cnpj = models.CharField(max_length=18, db_index=True)
    rg = models.CharField(max_length=20, blank=True, null=True)
    orgao_expedidor = models.CharField(max_length=20, blank=True, null=True)
    uf_rg = models.CharField(max_length=2, blank=True, null=True, verbose_name="UF do RG")
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
        ('APROVADO', 'Aprovado/Concluído'), # Este status agora serve de gatilho para o financeiro
        ('CANCELADO', 'Cancelado'),
    )

    STATUS_FINANCEIRO = (
        ('ABERTO', 'Aguardando Pagamento'),
        ('PAGO', 'Totalmente Pago'),
    )

    PAGADOR_DETRAN_CHOICES = (
        ('DESPACHANTE', 'Escritório Pagou (Reembolsável)'),
        ('CLIENTE', 'Cliente Pagou por Fora'),
    )

    despachante = models.ForeignKey('Despachante', on_delete=models.CASCADE)
    cliente = models.ForeignKey('Cliente', on_delete=models.CASCADE)
    veiculo = models.ForeignKey('Veiculo', on_delete=models.SET_NULL, null=True, blank=True)

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

    # --- FINANCEIRO: VALORES HERDADOS DO ORÇAMENTO ---
    valor_taxas_detran = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    valor_honorarios = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    # --- FINANCEIRO: CONTROLES INTERNOS (Calculados por alíquota) ---
    quem_pagou_detran = models.CharField(max_length=20, choices=PAGADOR_DETRAN_CHOICES, default='DESPACHANTE')
    custo_impostos = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    custo_taxa_bancaria = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    # --- FINANCEIRO: SITUAÇÃO DE PAGAMENTO ---
    status_financeiro = models.CharField(max_length=15, choices=STATUS_FINANCEIRO, default='ABERTO')
    data_pagamento = models.DateField(null=True, blank=True)

    observacoes_internas = models.TextField(blank=True, null=True)
    data_solicitacao = models.DateField(default=timezone.now)
    data_entrega = models.DateField(null=True, blank=True, verbose_name="Prazo de Entrega")

    def __str__(self):
        return f"{self.numero_atendimento or 'S/N'} - {self.cliente}"

    @property
    def valor_total_cliente(self):
        return self.valor_taxas_detran + self.valor_honorarios

    @property
    def lucro_liquido_real(self):
        return self.valor_honorarios - (self.custo_impostos + self.custo_taxa_bancaria)


class TipoServico(models.Model):
    despachante = models.ForeignKey(Despachante, on_delete=models.CASCADE)
    nome = models.CharField(max_length=100)  
    valor_base = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Custo DETRAN")
    honorarios = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Lucro/Honorários")
    ativo = models.BooleanField(default=True)

    def __str__(self):
        return self.nome

    @property
    def valor_total(self):
        return self.valor_base + self.honorarios


class Orcamento(models.Model):
    STATUS_ORCAMENTO = (
        ('PENDENTE', 'Pendente'),
        ('APROVADO', 'Aprovado (Gerou Processo)'),
        ('CANCELADO', 'Cancelado/Recusado'),
    )

    despachante = models.ForeignKey(Despachante, on_delete=models.CASCADE)
    cliente = models.ForeignKey(Cliente, on_delete=models.SET_NULL, null=True, blank=True)
    veiculo = models.ForeignKey(Veiculo, on_delete=models.SET_NULL, null=True, blank=True)
    nome_cliente_avulso = models.CharField(max_length=200, blank=True, null=True)

    data_criacao = models.DateTimeField(auto_now_add=True)
    validade = models.DateField(null=True, blank=True)
    
    observacoes = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_ORCAMENTO, default='PENDENTE')
    
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
        if self.cliente:
            return self.cliente.nome
        return self.nome_cliente_avulso or "Cliente Desconhecido"


class ItemOrcamento(models.Model):
    orcamento = models.ForeignKey(Orcamento, related_name='itens', on_delete=models.CASCADE)
    servico_nome = models.CharField(max_length=200)
    valor = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.servico_nome} - R$ {self.valor}"