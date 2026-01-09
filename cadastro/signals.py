# cadastro/signals.py

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.core.cache import cache
from .models import Atendimento

@receiver([post_save, post_delete], sender=Atendimento)
def limpar_cache_dashboard(sender, instance, **kwargs):
    """
    Sempre que um processo é salvo ou excluído, limpa o cache 
    do escritório dono desse processo para recalcular os totais.
    """
    if instance.despachante:
        despachante_id = instance.despachante.id
        cache_key = f"dashboard_stats_{despachante_id}"
        
        # Apaga a memória antiga
        cache.delete(cache_key)
        # print(f"Cache limpo para despachante {despachante_id}")