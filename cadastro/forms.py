from django import forms
from django.contrib.auth.models import User
from .models import Atendimento, Cliente, Veiculo, Despachante, PerfilUsuario

# ==============================================================================
# FORMULÁRIOS OPERACIONAIS
# ==============================================================================

from django import forms
from django.contrib.auth.models import User
from .models import Atendimento, Cliente, Veiculo

class AtendimentoForm(forms.ModelForm):
    class Meta:
        model = Atendimento
        fields = [
            'numero_atendimento',
            'cliente',
            'veiculo',
            'servico',
            'responsavel',
            'status',
            'data_entrega',
            'data_solicitacao',
            'observacoes_internas',
            'valor_taxas_detran',
            'valor_honorarios',
            'quem_pagou_detran',
            'custo_impostos',
            'custo_taxa_bancaria',
            'status_financeiro',
            'data_pagamento',
        ]
        widgets = {
            'numero_atendimento': forms.TextInput(attrs={'class': 'form-control'}),
            'cliente': forms.Select(attrs={'class': 'form-select'}),
            'veiculo': forms.Select(attrs={'class': 'form-select'}),
            'servico': forms.TextInput(attrs={'class': 'form-control'}),
            'responsavel': forms.Select(attrs={'class': 'form-select'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'data_entrega': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'}),
            'data_solicitacao': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'}),
            'observacoes_internas': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            
            'valor_taxas_detran': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'valor_honorarios': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'quem_pagou_detran': forms.Select(attrs={'class': 'form-select'}),
            
            # ATUALIZADO: Widgets de Custo agora são protegidos (ReadOnly)
            'custo_impostos': forms.NumberInput(attrs={
                'class': 'form-control bg-light', 
                'step': '0.01', 
                'readonly': 'readonly'
            }),
            'custo_taxa_bancaria': forms.NumberInput(attrs={
                'class': 'form-control bg-light', 
                'step': '0.01', 
                'readonly': 'readonly'
            }),
            
            'status_financeiro': forms.Select(attrs={'class': 'form-select'}),
            'data_pagamento': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'}),
        }

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # --- CORREÇÃO PRINCIPAL AQUI ---
        # Define que esses campos NÃO são obrigatórios na validação do formulário.
        # Isso permite salvar o form mesmo se o HTML enviar vazio (pois será calculado na View).
        self.fields['custo_impostos'].required = False
        self.fields['custo_taxa_bancaria'].required = False

        if user and hasattr(user, 'perfilusuario'):
            despachante = user.perfilusuario.despachante
            
            # Filtros de Segurança por Despachante
            self.fields['cliente'].queryset = Cliente.objects.filter(despachante=despachante)
            self.fields['veiculo'].queryset = Veiculo.objects.filter(despachante=despachante)
            
            # FILTRO DO RESPONSÁVEL
            self.fields['responsavel'].queryset = User.objects.filter(
                perfilusuario__despachante=despachante
            ).order_by('first_name')
            
            # Labels e Estilização
            self.fields['responsavel'].label_from_instance = lambda obj: f"{obj.get_full_name() or obj.username}".upper()
            self.fields['responsavel'].label = "Responsável Técnico"

            # Reforço visual de que impostos são calculados pelo sistema
            self.fields['custo_impostos'].help_text = "Calculado automaticamente sobre o honorário."
            self.fields['custo_taxa_bancaria'].help_text = "Taxa operacional provisionada pelo sistema."


class ClienteForm(forms.ModelForm):
    class Meta:
        model = Cliente
        exclude = ['despachante']
        UF_CHOICES = [
            ('', 'UF'), ('GO', 'GO'), ('DF', 'DF'), ('SP', 'SP'), ('MG', 'MG'), 
            ('TO', 'TO'), ('MT', 'MT'), ('MS', 'MS'), ('BA', 'BA'), ('RJ', 'RJ'),
            ('PR', 'PR'), ('RS', 'RS'), ('SC', 'SC'), ('ES', 'ES')
        ]
        widgets = {
            'nome': forms.TextInput(attrs={'class': 'form-control text-uppercase', 'id': 'nome'}),
            'cpf_cnpj': forms.TextInput(attrs={'class': 'form-control mask-cpf-cnpj', 'id': 'cpf_cnpj'}),
            'rg': forms.TextInput(attrs={'class': 'form-control text-uppercase', 'id': 'rg'}),
            'orgao_expedidor': forms.TextInput(attrs={'class': 'form-control text-uppercase', 'id': 'orgao_expedidor'}),
            'profissao': forms.TextInput(attrs={'class': 'form-control text-uppercase', 'id': 'profissao'}),
            'uf_rg': forms.Select(choices=UF_CHOICES, attrs={'class': 'form-select', 'id': 'uf_rg'}),
            'filiacao': forms.TextInput(attrs={'class': 'form-control text-uppercase', 'id': 'filiacao', 'placeholder': 'Nome da Mãe'}),
            'cep': forms.TextInput(attrs={'class': 'form-control mask-cep', 'id': 'cep'}),
            'rua': forms.TextInput(attrs={'class': 'form-control text-uppercase', 'id': 'rua'}),
            'numero': forms.TextInput(attrs={'class': 'form-control', 'id': 'numero'}),
            'bairro': forms.TextInput(attrs={'class': 'form-control text-uppercase', 'id': 'bairro'}),
            'cidade': forms.TextInput(attrs={'class': 'form-control text-uppercase', 'id': 'cidade'}),
            'uf': forms.TextInput(attrs={'class': 'form-control text-uppercase', 'id': 'uf'}),
            'complemento': forms.TextInput(attrs={'class': 'form-control text-uppercase', 'id': 'complemento'}),
            'telefone': forms.TextInput(attrs={'class': 'form-control mask-phone', 'id': 'telefone'}),
            'email': forms.EmailInput(attrs={'class': 'form-control lowercase', 'id': 'email'}),
        }


class VeiculoForm(forms.ModelForm):
    class Meta:
        model = Veiculo
        exclude = ['despachante']
        widgets = {
            'placa': forms.TextInput(attrs={'class': 'form-control'}),
            'renavam': forms.TextInput(attrs={'class': 'form-control'}),
            'chassi': forms.TextInput(attrs={'class': 'form-control'}),
            'marca': forms.TextInput(attrs={'class': 'form-control'}),
            'modelo': forms.TextInput(attrs={'class': 'form-control'}),
            'cor': forms.TextInput(attrs={'class': 'form-control'}),
            'ano_fabricacao': forms.NumberInput(attrs={'class': 'form-control'}),
            'ano_modelo': forms.NumberInput(attrs={'class': 'form-control'}),
            'tipo': forms.Select(attrs={'class': 'form-select'}),
            'cliente': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if user and hasattr(user, 'perfilusuario'):
            self.despachante = user.perfilusuario.despachante
            self.fields['cliente'].queryset = Cliente.objects.filter(despachante=self.despachante)
        else:
            self.despachante = None

    def clean_placa(self):
        if not self.despachante:
            return self.cleaned_data['placa']

        placa = self.cleaned_data['placa'].upper().replace('-', '').replace(' ', '')
        if Veiculo.objects.filter(despachante=self.despachante, placa=placa).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Esta placa já está cadastrada para este despachante.")
        return placa


class CompressaoPDFForm(forms.Form):
    arquivo_pdf = forms.FileField(
        label="Selecione o PDF",
        help_text="O sistema vai limpar metadados e otimizar a estrutura do arquivo.",
        widget=forms.ClearableFileInput(attrs={
            'class': 'form-control form-control-lg', 
            'accept': 'application/pdf'
        })
    )

# ==============================================================================
# NOVOS FORMULÁRIOS: PAINEL MASTER (SaaS)
# ==============================================================================

class DespachanteForm(forms.ModelForm):
    class Meta:
        model = Despachante
        fields = '__all__'
        exclude = ['asaas_customer_id', 'data_cadastro', 'ativo']
        widgets = {
            'nome_fantasia': forms.TextInput(attrs={'class': 'form-control'}),
            'razao_social': forms.TextInput(attrs={'class': 'form-control'}),
            'cnpj': forms.TextInput(attrs={'class': 'form-control mask-cnpj'}),
            'codigo_sindego': forms.TextInput(attrs={'class': 'form-control'}),
            'telefone': forms.TextInput(attrs={'class': 'form-control mask-phone'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'endereco_completo': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'dia_vencimento': forms.Select(attrs={'class': 'form-select'}),
            'valor_mensalidade': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'email_fatura': forms.EmailInput(attrs={'class': 'form-control'}),
            # --- CAMPOS DE CONFIGURAÇÃO SaaS ADICIONADOS ---
            'aliquota_imposto': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'taxa_bancaria_padrao': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }


class UsuarioMasterForm(forms.Form):
    """Formulário manual para criar usuários com senha já criptografada na View"""
    first_name = forms.CharField(label="Nome", widget=forms.TextInput(attrs={'class': 'form-control'}))
    last_name = forms.CharField(label="Sobrenome", widget=forms.TextInput(attrs={'class': 'form-control'}))
    
    username = forms.CharField(
        label="Nome de Usuário (Login)", 
        required=False,
        help_text="Opcional. Se deixar vazio, o login será igual ao e-mail.",
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ex: joao.silva'})
    )

    email = forms.EmailField(label="E-mail", widget=forms.EmailInput(attrs={'class': 'form-control'}))
    password = forms.CharField(label="Senha", widget=forms.PasswordInput(attrs={'class': 'form-control'}))
    
    despachante = forms.ModelChoiceField(
        queryset=Despachante.objects.all(),
        label="Vincular a Empresa",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    
    tipo_usuario = forms.ChoiceField(
        choices=PerfilUsuario.TIPO_CHOICES,
        label="Permissão",
        widget=forms.Select(attrs={'class': 'form-select'})
    )

class UsuarioMasterEditForm(UsuarioMasterForm):
    username = forms.CharField(label="Login", widget=forms.TextInput(attrs={'class': 'form-control', 'readonly': 'readonly'}))
    email = forms.EmailField(label="E-mail", widget=forms.EmailInput(attrs={'class': 'form-control', 'readonly': 'readonly'}))
    
    password = forms.CharField(
        label="Nova Senha", 
        required=False, 
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Deixe em branco para manter a atual'})
    )