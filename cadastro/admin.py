from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin
from django.utils.html import format_html
from django.contrib import messages
from django.core.mail import send_mail 
from django.utils import timezone      
from datetime import timedelta         
from django.conf import settings

from .models import Despachante, PerfilUsuario, Cliente, Veiculo, Atendimento
# Importamos as funções do asaas.py
from .asaas import criar_cliente_asaas, gerar_boleto_asaas

# --- 1. SEGURANÇA SAAS (O Filtro Mágico) ---
class SaasFilterMixin:
    """
    Garante que o usuário só veja dados do seu próprio Despachante.
    O Superusuário (Master) continua vendo tudo.
    """
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
class PerfilUsuarioInline(admin.StackedInline):
    model = PerfilUsuario
    can_delete = False
    verbose_name_plural = 'Perfil, Permissões e Assinatura'
    fk_name = 'user'

class CustomUserAdmin(UserAdmin):
    inlines = (PerfilUsuarioInline, )
    list_display = ('username', 'email', 'get_despachante', 'get_status_assinatura', 'is_active')
    list_filter = ('is_active', 'is_staff', 'perfilusuario__despachante') 

    def get_despachante(self, instance):
        if hasattr(instance, 'perfilusuario') and instance.perfilusuario.despachante:
            return instance.perfilusuario.despachante.nome_fantasia
        return "-"
    get_despachante.short_description = 'Despachante'

    def get_status_assinatura(self, instance):
        if not hasattr(instance, 'perfilusuario'):
            return "-"
        dias = instance.perfilusuario.get_dias_restantes()
        
        if dias is None:
            return format_html('<span style="color:blue; font-weight:bold;">{}</span>', '♾️ Vitalício')
        if dias < 0:
            return format_html('<span style="color:red; font-weight:bold;">⛔ VENCIDO (há {} dias)</span>', abs(dias))
        elif dias <= 5:
            return format_html('<span style="color:orange; font-weight:bold;">⚠️ Vence em {} dias</span>', dias)
        else:
            return format_html('<span style="color:green; font-weight:bold;">✅ Ativo ({} dias)</span>', dias)

    get_status_assinatura.short_description = 'Status / Validade'

admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)


# --- 3. SEUS MODELOS DE NEGÓCIO ---

@admin.register(Despachante)
class DespachanteAdmin(admin.ModelAdmin):
    # --- AQUI ESTÁ A ATUALIZAÇÃO ---
    # Adicionei 'razao_social' para aparecer na lista
    list_display = ('nome_fantasia', 'razao_social', 'codigo_sindego', 'cnpj', 'status_financeiro', 'ativo')
    
    # Adicionei também na busca para facilitar encontrar a empresa
    search_fields = ('nome_fantasia', 'razao_social', 'cnpj', 'codigo_sindego')
    
    actions = ['gerar_cadastro_asaas', 'gerar_fatura_e_renovar_30_dias']

    def status_financeiro(self, obj):
        if obj.asaas_customer_id:
            return "🟢 Integrado"
        return "🔴 Pendente"
    status_financeiro.short_description = "Status Asaas"

    # --- AÇÃO 1: APENAS SINCRONIZAR CADASTRO ---
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

    # --- AÇÃO 2: GERAR FATURA + EMAIL + RENOVAR ACESSO ---
    @admin.action(description='💰 Gerar Fatura, Enviar E-mail e Renovar (+30 dias)')
    def gerar_fatura_e_renovar_30_dias(self, request, queryset):
        sucesso = 0
        erros = 0

        for despachante in queryset:
            # 1. Garante cadastro no Asaas
            if not despachante.asaas_customer_id:
                criar_cliente_asaas(despachante)
            
            # 2. Gera o Boleto
            resultado = gerar_boleto_asaas(despachante)

            if resultado['sucesso']:
                link_pdf = resultado['link_boleto']
                link_pagar = resultado['link_fatura']

                # 3. Monta e Envia o E-mail
                assunto = f"Fatura Disponível - {despachante.nome_fantasia}"
                mensagem = f"""
                Olá, {despachante.nome_fantasia}!

                Sua mensalidade do sistema DespachaPro foi gerada.
                
                Valor: R$ {despachante.valor_mensalidade}
                
                📄 Baixar Boleto PDF: {link_pdf}
                💳 Pagar (Pix/Boleto): {link_pagar}

                Seu acesso ao sistema foi renovado preventivamente por mais 30 dias.
                
                Att,
                Equipe DespachaPro
                """
                
                try:
                    email_destino = despachante.email_fatura or despachante.email
                    send_mail(
                        assunto,
                        mensagem,
                        settings.DEFAULT_FROM_EMAIL or 'financeiro@seusistema.com.br',
                        [email_destino],
                        fail_silently=False,
                    )
                except Exception as e:
                    self.message_user(request, f"Fatura gerada, mas erro ao enviar email para {despachante}: {e}", level=messages.WARNING)

                # 4. Renova o acesso dos usuários deste despachante
                funcionarios = PerfilUsuario.objects.filter(despachante=despachante)
                hoje = timezone.now().date()
                dias_liberados = 30
                
                for perfil in funcionarios:
                    if not perfil.data_expiracao or perfil.data_expiracao < hoje:
                        perfil.data_expiracao = hoje + timedelta(days=dias_liberados)
                    else:
                        perfil.data_expiracao = perfil.data_expiracao + timedelta(days=dias_liberados)
                    perfil.save()

                sucesso += 1
            else:
                erros += 1
                self.message_user(request, f"Erro Asaas ({despachante}): {resultado.get('erro')}", level=messages.ERROR)

        self.message_user(request, f"Processo finalizado: {sucesso} faturas enviadas e renovadas.", level=messages.SUCCESS)


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