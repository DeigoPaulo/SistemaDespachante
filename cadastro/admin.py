from django.contrib import admin
from .models import Despachante, PerfilUsuario, Cliente, Veiculo, Atendimento

class PerfilUsuarioInline(admin.StackedInline):
    model = PerfilUsuario
    can_delete = False
    verbose_name_plural = 'Perfil do Usuário'

@admin.register(Despachante)
class DespachanteAdmin(admin.ModelAdmin):
    list_display = ('nome_fantasia', 'codigo_sindego', 'cnpj', 'ativo')
    search_fields = ('nome_fantasia', 'cnpj', 'codigo_sindego')
    inlines = [PerfilUsuarioInline]

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