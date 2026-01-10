from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('cadastro.urls')), # Redireciona para o app cadastro
]

# --- CONFIGURAÇÃO PARA SERVIR ARQUIVOS DE MÍDIA (LOGOS/FOTOS) ---
# Isso permite que as imagens apareçam durante o desenvolvimento (DEBUG=True)
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)