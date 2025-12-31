from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin
from django.utils.html import format_html
from django.contrib import messages  # Importante para as mensagens de sucesso/erro
from .models import Despachante, PerfilUsuario, Cliente, Veiculo, Atendimento
# Certifique-se de que o arquivo asaas.py existe na mesma pasta
from .asaas import criar_cliente_asaas 

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
    # Adicionei 'asaas_customer_id' na lista para você ver se deu certo
    list_display = ('nome_fantasia', 'codigo_sindego', 'cnpj', 'asaas_customer_id', 'ativo')
    search_fields = ('nome_fantasia', 'cnpj', 'codigo_sindego')
    
    # --- AQUI ESTÁ A NOVIDADE: O BOTÃO DO ASAAS ---
    actions = ['gerar_cadastro_asaas']

    @admin.action(description='💰 Criar/Sincronizar Cliente no Asaas')
    def gerar_cadastro_asaas(self, request, queryset):
        sucesso = 0
        erros = 0
        
        for despachante in queryset:
            # Chama a função que criamos no arquivo asaas.py
            novo_id = criar_cliente_asaas(despachante)
            
            if novo_id:
                # O update acontece dentro da função criar_cliente_asaas geralmente,
                # mas aqui garantimos que o admin saiba que o ID existe.
                # Se sua função asaas.py já salva o objeto, ótimo.
                # Se ela só retorna a string, precisamos salvar aqui:
                if despachante.asaas_customer_id != novo_id:
                    despachante.asaas_customer_id = novo_id
                    despachante.save()
                sucesso += 1
            else:
                erros += 1
        
        # Mensagem feedback para você no topo da tela
        if erros > 0:
            self.message_user(request, f"{sucesso} sincronizados. {erros} erros. Verifique o console/logs.", messages.WARNING)
        else:
            self.message_user(request, f"{sucesso} despachantes sincronizados com sucesso no Asaas!", messages.SUCCESS)


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