from django import forms
from .models import Atendimento, Cliente, Veiculo

class AtendimentoForm(forms.ModelForm):
    class Meta:
        model = Atendimento
        fields = [
            'numero_atendimento',
            'cliente',
            'veiculo',
            'servico',
            'status',
            'data_entrega',      # Adicionado
            'data_solicitacao',  # Adicionado (caso precise corrigir a data de entrada)
            'observacoes_internas'
        ]
        widgets = {
            'numero_atendimento': forms.TextInput(attrs={'class': 'form-control'}),
            'cliente': forms.Select(attrs={'class': 'form-select'}),
            'veiculo': forms.Select(attrs={'class': 'form-select'}),
            'servico': forms.TextInput(attrs={'class': 'form-control'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            
            # --- Configuração dos Campos de Data ---
            'data_entrega': forms.DateInput(
                format='%Y-%m-%d',  # Importante para a data aparecer ao editar
                attrs={'type': 'date', 'class': 'form-control'}
            ),
            'data_solicitacao': forms.DateInput(
                format='%Y-%m-%d',
                attrs={'type': 'date', 'class': 'form-control'}
            ),
            # ---------------------------------------

            'observacoes_internas': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3
            }),
        }

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if user and hasattr(user, 'perfilusuario'):
            despachante = user.perfilusuario.despachante
            self.fields['cliente'].queryset = Cliente.objects.filter(despachante=despachante)
            self.fields['veiculo'].queryset = Veiculo.objects.filter(despachante=despachante)


class ClienteForm(forms.ModelForm):
    class Meta:
        model = Cliente
        exclude = ['despachante']
        
        # Lista de estados para o campo UF do RG
        # Você pode adicionar mais estados aqui se precisar
        UF_CHOICES = [
            ('', 'UF'), ('GO', 'GO'), ('DF', 'DF'), ('SP', 'SP'), ('MG', 'MG'), 
            ('TO', 'TO'), ('MT', 'MT'), ('MS', 'MS'), ('BA', 'BA'), ('RJ', 'RJ'),
            ('PR', 'PR'), ('RS', 'RS'), ('SC', 'SC'), ('ES', 'ES')
        ]

        widgets = {
            # --- DADOS PESSOAIS ---
            'nome': forms.TextInput(attrs={'class': 'form-control text-uppercase', 'id': 'nome'}),
            'cpf_cnpj': forms.TextInput(attrs={'class': 'form-control mask-cpf-cnpj', 'id': 'cpf_cnpj'}),
            'rg': forms.TextInput(attrs={'class': 'form-control text-uppercase', 'id': 'rg'}),
            'orgao_expedidor': forms.TextInput(attrs={'class': 'form-control text-uppercase', 'id': 'orgao_expedidor'}),
            'profissao': forms.TextInput(attrs={'class': 'form-control text-uppercase', 'id': 'profissao'}),
            
            # --- NOVOS CAMPOS ---
            'uf_rg': forms.Select(choices=UF_CHOICES, attrs={'class': 'form-select', 'id': 'uf_rg'}),
            'filiacao': forms.TextInput(attrs={'class': 'form-control text-uppercase', 'id': 'filiacao', 'placeholder': 'Nome da Mãe'}),

            # --- ENDEREÇO ---
            'cep': forms.TextInput(attrs={'class': 'form-control mask-cep', 'id': 'cep'}),
            'rua': forms.TextInput(attrs={'class': 'form-control text-uppercase', 'id': 'rua'}),
            'numero': forms.TextInput(attrs={'class': 'form-control', 'id': 'numero'}),
            'bairro': forms.TextInput(attrs={'class': 'form-control text-uppercase', 'id': 'bairro'}),
            'cidade': forms.TextInput(attrs={'class': 'form-control text-uppercase', 'id': 'cidade'}),
            'uf': forms.TextInput(attrs={'class': 'form-control text-uppercase', 'id': 'uf'}), # UF do Endereço
            'complemento': forms.TextInput(attrs={'class': 'form-control text-uppercase', 'id': 'complemento'}),

            # --- CONTATO ---
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
        despachante = user.perfilusuario.despachante
        self.despachante = despachante
        self.fields['cliente'].queryset = Cliente.objects.filter(despachante=despachante)

    def clean_placa(self):
        placa = self.cleaned_data['placa'].upper().replace('-', '').replace(' ', '')

        if Veiculo.objects.filter(
            despachante=self.despachante,
            placa=placa
        ).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(
                "Esta placa já está cadastrada para este despachante."
            )

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