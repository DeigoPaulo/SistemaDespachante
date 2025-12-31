from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin
from django.utils.html import format_html
from django.utils import timezone
from .models import Despachante, PerfilUsuario, Cliente, Veiculo, Atendimento

# 1. Inline do Perfil (Usado tanto no Despachante quanto no Usuário)
class PerfilUsuarioInline(admin.StackedInline):
    model = PerfilUsuario
    can_delete = False
    verbose_name_plural = 'Perfil, Permissões e Assinatura'
    fk_name = 'user' # Garante que o inline saiba conectar com o User

# 2. SEU CÓDIGO ORIGINAL (Mantido Intacto)
@admin.register(Despachante)
class DespachanteAdmin(admin.ModelAdmin):
    list_display = ('nome_fantasia', 'codigo_sindego', 'cnpj', 'ativo')
    search_fields = ('nome_fantasia', 'cnpj', 'codigo_sindego')
    # Nota: Removi o inline daqui para evitar confusão na edição, 
    # mas se quiser ver os funcionários dentro do Despachante, pode descomentar:
    # inlines = [PerfilUsuarioInline] 

@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ('nome', 'cpf_cnpj', 'cidade', 'despachante')
    list_filter = ('despachante', 'cidade')
    search_fields = ('nome', 'cpf_cnpj')

@admin.register(Veiculo)
class VeiculoAdmin(admin.ModelAdmin):
    list_display = ('placa', 'modelo', 'cor', 'cliente', 'despachante')
    search_fields = ('placa', 'chassi', 'renavam')
    list_filter = ('tipo', 'despachante')

@admin.register(Atendimento)
class AtendimentoAdmin(admin.ModelAdmin):
    list_display = ('numero_atendimento', 'cliente', 'veiculo', 'servico', 'status', 'data_solicitacao')
    list_filter = ('status', 'data_solicitacao', 'despachante')
    search_fields = ('numero_atendimento', 'cliente__nome', 'veiculo__placa')
    date_hierarchy = 'data_solicitacao'

# 3. A NOVIDADE: O "Painel Master" de Controle de Assinaturas
# Isso substitui a administração padrão de usuários do Django
class CustomUserAdmin(UserAdmin):
    inlines = (PerfilUsuarioInline, ) # Aqui você edita a data de expiração
    
    # Adicionamos colunas novas na lista de usuários
    list_display = ('username', 'email', 'get_despachante', 'get_status_assinatura', 'is_active')
    
    # Filtros laterais para te ajudar a achar quem venceu
    list_filter = ('is_active', 'is_staff', 'perfilusuario__despachante') 

    # Coluna para mostrar de qual Despachante é o usuário
    def get_despachante(self, instance):
        if hasattr(instance, 'perfilusuario') and instance.perfilusuario.despachante:
            return instance.perfilusuario.despachante.nome_fantasia
        return "-"
    get_despachante.short_description = 'Despachante'

    # Coluna do "Semáforo" (Cores e Prazos)
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

# Desregistra o User padrão e coloca o nosso turbinado
admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)