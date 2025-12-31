from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin
from django.utils.html import format_html
from django.utils import timezone
from .models import Despachante, PerfilUsuario, Cliente, Veiculo, Atendimento

# --- 1. SEGURANÇA SAAS (O Filtro Mágico) ---
# Esta classe precisa vir antes das outras para funcionar a herança
class SaasFilterMixin:
    """
    Garante que o usuário só veja dados do seu próprio Despachante.
    O Superusuário (Master) continua vendo tudo.
    """
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # Se for você (Master), mostra tudo
        if request.user.is_superuser:
            return qs
        # Se for cliente/operador, filtra pelo despachante dele
        if hasattr(request.user, 'perfilusuario') and request.user.perfilusuario.despachante:
            return qs.filter(despachante=request.user.perfilusuario.despachante)
        return qs.none() # Se não tiver perfil configurado, não vê nada (segurança)

    def save_model(self, request, obj, form, change):
        """Ao salvar, força o despachante do usuário logado (se não for Master)"""
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

# Atualiza o Admin de Usuários
admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)


# --- 3. SEUS MODELOS DE NEGÓCIO (Com a Blindagem Aplicada) ---

@admin.register(Despachante)
class DespachanteAdmin(admin.ModelAdmin):
    # Despachante não usa o Mixin, pois só o Master acessa essa tela
    list_display = ('nome_fantasia', 'codigo_sindego', 'cnpj', 'ativo')
    search_fields = ('nome_fantasia', 'cnpj', 'codigo_sindego')

@admin.register(Cliente)
class ClienteAdmin(SaasFilterMixin, admin.ModelAdmin): # <--- Blindagem aqui
    list_display = ('nome', 'cpf_cnpj', 'cidade', 'get_despachante_view')
    list_filter = ('despachante', 'cidade')
    search_fields = ('nome', 'cpf_cnpj')

    # Helper para você (Master) ver de quem é o cliente na lista
    def get_despachante_view(self, instance):
        return instance.despachante.nome_fantasia if instance.despachante else '-'
    get_despachante_view.short_description = 'Despachante'

@admin.register(Veiculo)
class VeiculoAdmin(SaasFilterMixin, admin.ModelAdmin): # <--- Blindagem aqui
    list_display = ('placa', 'modelo', 'cor', 'cliente', 'get_despachante_view')
    search_fields = ('placa', 'chassi', 'renavam')
    list_filter = ('tipo', 'despachante')

    def get_despachante_view(self, instance):
        return instance.despachante.nome_fantasia if instance.despachante else '-'
    get_despachante_view.short_description = 'Despachante'

@admin.register(Atendimento)
class AtendimentoAdmin(SaasFilterMixin, admin.ModelAdmin): # <--- Blindagem aqui
    list_display = ('numero_atendimento', 'cliente', 'veiculo', 'servico', 'status', 'data_solicitacao')
    list_filter = ('status', 'data_solicitacao', 'despachante')
    search_fields = ('numero_atendimento', 'cliente__nome', 'veiculo__placa')
    date_hierarchy = 'data_solicitacao'