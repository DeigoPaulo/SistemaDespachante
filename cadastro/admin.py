from django import forms
from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import password_validation 
from django.utils.html import format_html
from django.contrib import messages
from django.core.mail import send_mail 
from django.utils import timezone      
from datetime import timedelta         
from django.conf import settings
from .models import BaseConhecimento

from .models import Despachante, PerfilUsuario, Cliente, Veiculo, Atendimento
from .asaas import criar_cliente_asaas, gerar_boleto_asaas

# --- 1. SEGURANÇA SAAS ---
class SaasFilterMixin:
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        if hasattr(request.user, 'perfilusuario') and request.user.perfilusuario.despachante:
            return qs.filter(despachante=request.user.perfilusuario.despachante)
        return qs.none()

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser and not getattr(obj, 'despachante_id', None):
            if hasattr(request.user, 'perfilusuario'):
                obj.despachante = request.user.perfilusuario.despachante
        super().save_model(request, obj, form, change)


# --- 2. CONFIGURAÇÕES DO USUÁRIO ---

class UsuarioCriacaoForm(UserCreationForm):
    email = forms.EmailField(required=True, label="Endereço de e-mail")
    first_name = forms.CharField(required=False, label="Primeiro nome")
    last_name = forms.CharField(required=False, label="Sobrenome")

    password_1 = forms.CharField(
        label="Senha", widget=forms.PasswordInput, strip=False,
        help_text=password_validation.password_validators_help_text_html(),
    )
    password_2 = forms.CharField(
        label="Confirmação de senha", widget=forms.PasswordInput, strip=False,
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email', 'first_name', 'last_name')

    def clean_password_2(self):
        pass1 = self.cleaned_data.get("password_1")
        pass2 = self.cleaned_data.get("password_2")
        if pass1 and pass2 and pass1 != pass2:
            raise forms.ValidationError("As senhas não conferem.")
        return pass2

    def save(self, commit=True):
        user = super(UserCreationForm, self).save(commit=False)
        user.set_password(self.cleaned_data["password_1"])
        if commit:
            user.save()
        return user


class PerfilUsuarioInline(admin.StackedInline):
    model = PerfilUsuario
    can_delete = False
    verbose_name_plural = 'Vincular ao Despachante (Perfil)'
    fk_name = 'user'
    help_text = "Selecione aqui a qual Despachante este operador pertence."

class CustomUserAdmin(UserAdmin):
    add_form = UsuarioCriacaoForm
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'email', 'first_name', 'last_name', 'password_1', 'password_2'),
        }),
    )
    
    inlines = (PerfilUsuarioInline, )
    list_display = ('username', 'email', 'get_despachante', 'get_status_assinatura', 'is_active')
    list_filter = ('is_active', 'is_staff', 'perfilusuario__despachante') 
    search_fields = ('username', 'first_name', 'email', 'perfilusuario__despachante__nome_fantasia')
    
    actions = ['conceder_15_dias']

    @admin.action(description='🎁 Conceder 15 dias para a EMPRESA do Usuário')
    def conceder_15_dias(self, request, queryset):
        count = 0
        hoje = timezone.now().date()
        despachantes_afetados = set()
        
        for user in queryset:
            if hasattr(user, 'perfilusuario') and user.perfilusuario.despachante:
                despachante = user.perfilusuario.despachante
                
                if despachante.id not in despachantes_afetados:
                    if not despachante.data_validade_sistema or despachante.data_validade_sistema < hoje:
                        despachante.data_validade_sistema = hoje + timedelta(days=15)
                    else:
                        despachante.data_validade_sistema += timedelta(days=15)
                    
                    despachante.ativo = True
                    despachante.save()
                    despachantes_afetados.add(despachante.id)
                    count += 1
                    
        self.message_user(request, f"Validade estendida para {count} empresas vinculadas aos usuários selecionados.", messages.SUCCESS)

    def get_despachante(self, instance):
        if hasattr(instance, 'perfilusuario') and instance.perfilusuario.despachante:
            return instance.perfilusuario.despachante.nome_fantasia
        return "-"
    get_despachante.short_description = 'Despachante'

    def get_status_assinatura(self, instance):
        if not hasattr(instance, 'perfilusuario') or not instance.perfilusuario.despachante:
            return "-"
            
        despachante = instance.perfilusuario.despachante
        dias = despachante.get_dias_restantes()
        
        if dias is None: 
            return format_html('<span style="color:blue;">{}</span>', '♾️ Vitalício')
        
        if dias < 0: 
            return format_html('<span style="color:red; font-weight:bold;">⛔ Empresa Vencida ({}d)</span>', abs(dias))
        elif dias <= 5: 
            return format_html('<span style="color:orange; font-weight:bold;">⚠️ {} dias</span>', dias)
        
        return format_html('<span style="color:green;">✅ {} dias</span>', dias)
    
    get_status_assinatura.short_description = 'Validade (Empresa)'

admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)


# --- 3. SEUS MODELOS DE NEGÓCIO ---

@admin.register(Despachante)
class DespachanteAdmin(admin.ModelAdmin):
    list_display = ('nome_fantasia', 'razao_social', 'cnpj', 'plano', 'get_validade', 'status_financeiro', 'ativo')
    search_fields = ('nome_fantasia', 'razao_social', 'cnpj', 'codigo_sindego')
    list_filter = ('plano', 'ativo', 'dia_vencimento')
    
    # Campos visíveis na edição
    fields = (
        'nome_fantasia', 'razao_social', 'cnpj', 'codigo_sindego', 
        'telefone', 'email', 'endereco_completo', 'logo', 
        'ativo', 'plano', 'data_validade_sistema', 
        'valor_mensalidade', 'dia_vencimento', 'email_fatura',
        'asaas_customer_id' 
    )
    
    readonly_fields = ('status_financeiro_detalhe',)
    
    actions = [
        'confirmar_pagamento_manual', 
        'gerar_cadastro_asaas', 
        'gerar_fatura_e_renovar_30_dias', 
        'conceder_cortesia_manual'
    ]

    def status_financeiro(self, obj):
        if obj.asaas_customer_id: return "🟢 Integrado"
        return "🔴 Pendente"
    status_financeiro.short_description = "Asaas"

    def status_financeiro_detalhe(self, obj):
        if obj.asaas_customer_id:
            return format_html('<span style="color:green; font-weight:bold;">CLIENTE INTEGRADO (ID: {})</span>', obj.asaas_customer_id)
        return format_html('<span style="color:red;">{}</span>', 'NÃO INTEGRADO - Use a ação "Sincronizar" na lista.')
    status_financeiro_detalhe.short_description = "Status da Integração"

    def get_validade(self, obj):
        dias = obj.get_dias_restantes()
        
        if dias is None: 
            return format_html('<span style="color:blue; font-weight:bold;">{}</span>', '♾️ Vitalício')
        
        if dias < 0: 
            return format_html('<span style="color:red; font-weight:bold;">⛔ Vencido há {} dias</span>', abs(dias))
        
        if dias <= 5:
            return format_html('<span style="color:orange; font-weight:bold;">⚠️ {} dias</span>', dias)

        return format_html('<span style="color:green;">✅ {} dias</span>', dias)
    get_validade.short_description = "Assinatura"

    # --- AÇÕES ---

    @admin.action(description='✅ Confirmar Pagamento Manual (+30 dias)')
    def confirmar_pagamento_manual(self, request, queryset):
        """
        Adiciona 30 dias na validade.
        Lógica:
        - Novo Cliente: Começa de hoje.
        - Dívida Antiga (>60 dias): Zera e começa de hoje (Reativação).
        - Renovação Normal: Soma +30 dias na data existente (Mantém ciclo).
        """
        sucesso = 0
        hoje = timezone.now().date()
        limite_reativacao = hoje - timedelta(days=60) # Limite de "dívida perdoada"
        
        for despachante in queryset:
            data_atual = despachante.data_validade_sistema
            
            # 1. Cliente Novo
            if not data_atual:
                despachante.data_validade_sistema = hoje + timedelta(days=30)
                
            # 2. Dívida Muito Antiga (Reativação)
            elif data_atual < limite_reativacao:
                despachante.data_validade_sistema = hoje + timedelta(days=30)
                
            # 3. Renovação Normal (Ciclo)
            else:
                despachante.data_validade_sistema += timedelta(days=30)
            
            despachante.ativo = True 
            despachante.save()
            sucesso += 1
            
        self.message_user(request, f"Pagamento confirmado para {sucesso} empresas. Acesso renovado!", messages.SUCCESS)

    @admin.action(description='🎁 Conceder 20 dias de Cortesia (Renovação Manual)')
    def conceder_cortesia_manual(self, request, queryset):
        sucesso = 0
        hoje = timezone.now().date()
        
        for despachante in queryset:
            if not despachante.data_validade_sistema or despachante.data_validade_sistema < hoje:
                despachante.data_validade_sistema = hoje + timedelta(days=20)
            else:
                despachante.data_validade_sistema += timedelta(days=20)
            
            despachante.ativo = True 
            despachante.save()
            sucesso += 1
            
        self.message_user(request, f"Cortesia aplicada: {sucesso} empresas renovadas por +20 dias.", messages.SUCCESS)

    @admin.action(description='🔄 Sincronizar Cliente no Asaas (Sem Cobrança)')
    def gerar_cadastro_asaas(self, request, queryset):
        sucesso = 0
        erros = 0
        for despachante in queryset:
            novo_id = criar_cliente_asaas(despachante)
            if novo_id:
                if despachante.asaas_customer_id != novo_id:
                    despachante.asaas_customer_id = novo_id
                    despachante.save()
                sucesso += 1
            else:
                erros += 1
        if erros > 0:
            self.message_user(request, f"{sucesso} sinc. {erros} erros.", messages.WARNING)
        else:
            self.message_user(request, f"{sucesso} despachantes sincronizados!", messages.SUCCESS)

    @admin.action(description='💰 Gerar Fatura, Enviar E-mail e Renovar (+30 dias)')
    def gerar_fatura_e_renovar_30_dias(self, request, queryset):
        sucesso = 0
        erros = 0
        for despachante in queryset:
            if not despachante.asaas_customer_id:
                criar_cliente_asaas(despachante)
            
            # [CORREÇÃO] Chamada SEM parâmetros de data.
            # O sistema vai ler o 'dia_vencimento' do cliente no banco.
            resultado = gerar_boleto_asaas(despachante)

            if resultado['sucesso']:
                link_pdf = resultado['link_boleto']
                link_pagar = resultado['link_fatura']
                assunto = f"Fatura Disponível - {despachante.nome_fantasia}"
                mensagem = f"""
                Olá, {despachante.nome_fantasia}!
                Sua mensalidade foi gerada. Valor: R$ {despachante.valor_mensalidade}
                📄 Boleto: {link_pdf} | 💳 Pagar: {link_pagar}
                Acesso renovado por 30 dias.
                """
                try:
                    email_destino = despachante.email_fatura or despachante.email
                    if email_destino:
                        send_mail(assunto, mensagem, settings.DEFAULT_FROM_EMAIL or 'financeiro@seusistema.com.br', [email_destino], fail_silently=False)
                except Exception as e:
                    self.message_user(request, f"Erro ao enviar email para {despachante}: {e}", level=messages.WARNING)

                # [ATUALIZADO] Mesma lógica de segurança da Baixa Manual
                hoje = timezone.now().date()
                limite_reativacao = hoje - timedelta(days=60)
                data_atual = despachante.data_validade_sistema

                if not data_atual:
                    despachante.data_validade_sistema = hoje + timedelta(days=30)
                elif data_atual < limite_reativacao:
                    despachante.data_validade_sistema = hoje + timedelta(days=30)
                else:
                    despachante.data_validade_sistema += timedelta(days=30)
                
                despachante.ativo = True
                despachante.save()
                sucesso += 1
            else:
                erros += 1
                self.message_user(request, f"Erro Asaas ({despachante}): {resultado.get('erro')}", level=messages.ERROR)
        
        self.message_user(request, f"Processo finalizado: {sucesso} renovações realizadas.", level=messages.SUCCESS)


@admin.register(Cliente)
class ClienteAdmin(SaasFilterMixin, admin.ModelAdmin):
    list_display = ('nome', 'cpf_cnpj', 'cidade', 'get_despachante_view')
    list_filter = ('despachante', 'cidade')
    search_fields = ('nome', 'cpf_cnpj')
    def get_despachante_view(self, instance):
        return instance.despachante.nome_fantasia if instance.despachante else '-'
    get_despachante_view.short_description = 'Despachante'

@admin.register(Veiculo)
class VeiculoAdmin(SaasFilterMixin, admin.ModelAdmin):
    list_display = ('placa', 'modelo', 'cor', 'cliente', 'get_despachante_view')
    search_fields = ('placa', 'chassi', 'renavam')
    list_filter = ('tipo', 'despachante')
    def get_despachante_view(self, instance):
        return instance.despachante.nome_fantasia if instance.despachante else '-'
    get_despachante_view.short_description = 'Despachante'

@admin.register(Atendimento)
class AtendimentoAdmin(SaasFilterMixin, admin.ModelAdmin):
    list_display = ('numero_atendimento', 'cliente', 'veiculo', 'servico', 'status', 'data_solicitacao')
    list_filter = ('status', 'data_solicitacao', 'despachante')
    search_fields = ('numero_atendimento', 'cliente__nome', 'veiculo__placa')
    date_hierarchy = 'data_solicitacao'

@admin.register(BaseConhecimento)
class BaseConhecimentoAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'categoria', 'data_atualizacao', 'ativo')
    list_filter = ('categoria', 'ativo')
    search_fields = ('titulo', 'conteudo', 'palavras_chave')
    
    readonly_fields = ('data_atualizacao',)

    fieldsets = (
        ('Identificação', {
            'fields': ('titulo', 'categoria', 'palavras_chave', 'ativo')
        }),
        ('O Conhecimento (Cérebro da IA)', {
            'description': 'Escreva aqui EXATAMENTE o procedimento correto. A IA usará este texto para responder o usuário.',
            'fields': ('conteudo',)
        }),
    )