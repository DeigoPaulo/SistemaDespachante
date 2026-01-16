"""
Django settings for config project.
BLINDADO E SEGURO (ZERO SENHAS NO CÓDIGO)
"""

from pathlib import Path
import os
from dotenv import load_dotenv 
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

# ==============================================================================
# 1. CARREGAMENTO DE VARIÁVEIS DE AMBIENTE
# ==============================================================================
BASE_DIR = Path(__file__).resolve().parent.parent

# Carrega o .env (Onde estão as senhas reais)
load_dotenv(BASE_DIR / '.env')

# ==============================================================================
# 2. SEGURANÇA
# ==============================================================================
# A chave vem do .env. Se não existir, usa uma insegura SÓ para não travar localmente.
SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-fallback-dev-only')

# Debug controlado pelo arquivo .env
DEBUG = os.getenv('DEBUG', 'False') == 'True'

ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', '127.0.0.1,localhost').split(',')


# ==============================================================================
# 3. APLICAÇÕES INSTALADAS
# ==============================================================================
INSTALLED_APPS = [
    # "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize', 
    'cadastro',
]

SESSION_ENGINE = 'django.contrib.sessions.backends.db'
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    "whitenoise.middleware.WhiteNoiseMiddleware",
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'


# ==============================================================================
# 4. TEMPLATES
# ==============================================================================
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'], 
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# ==============================================================================
# 5. BANCO DE DADOS
# ==============================================================================
# Padrão: SQLite (Arquivo local)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# --- OPÇÃO POSTGRESQL (Para o futuro/Produção) ---
# Note que agora usamos os.getenv. NENHUMA senha fica escrita aqui.
# Para ativar, basta descomentar e configurar as variáveis no arquivo .env

# DATABASES = {
#    'default': {
#        'ENGINE': 'django.db.backends.postgresql',
#        'NAME': os.getenv('DB_NAME', 'sistema_despachante_db'),
#        'USER': os.getenv('DB_USER', 'postgres'),
#        'PASSWORD': os.getenv('DB_PASSWORD'), # <--- A senha vem do cofre (.env)
#        'HOST': os.getenv('DB_HOST', 'localhost'),
#        'PORT': os.getenv('DB_PORT', '5432'),
#    }
# }


# ==============================================================================
# 6. VALIDAÇÃO DE SENHA
# ==============================================================================
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# ==============================================================================
# 7. INTERNACIONALIZAÇÃO
# ==============================================================================
LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True


# ==============================================================================
# 8. ARQUIVOS ESTÁTICOS E MÍDIA
# ==============================================================================
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'


# ==============================================================================
# 9. SISTEMA
# ==============================================================================
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'
LOGIN_URL = 'login'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# --- VISUAL (UNFOLD) ---
UNFOLD = {
    "SITE_TITLE": "Sistema Despachante",
    "SITE_HEADER": "Painel de Gestão",
    "SITE_URL": "/",
    "SITE_SYMBOL": "speed",
    "COLORS": {
        "primary": {
            "50": "239 246 255",
            "100": "219 234 254",
            "200": "191 219 254",
            "300": "147 197 253",
            "400": "96 165 250",
            "500": "59 130 246",
            "600": "37 99 235",
            "700": "29 78 216",
            "800": "30 64 175",
            "900": "30 58 138",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
        "navigation": [
            {
                "title": "Gestão Operacional",
                "separator": True,
                "items": [
                    {
                        "title": "Clientes",
                        "icon": "person",
                        "link": reverse_lazy("admin:cadastro_cliente_changelist"),
                    },
                    {
                        "title": "Veículos",
                        "icon": "directions_car",
                        "link": reverse_lazy("admin:cadastro_veiculo_changelist"),
                    },
                    {
                        "title": "Atendimentos",
                        "icon": "assignment",
                        "link": reverse_lazy("admin:cadastro_atendimento_changelist"),
                    },
                ],
            },
            {
                "title": "Administração do Sistema",
                "separator": True,
                "collapse": True,
                "items": [
                    {
                        "title": "Usuários e Acessos",
                        "icon": "group",
                        "link": reverse_lazy("admin:auth_user_changelist"),
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "Despachantes (Empresas)",
                        "icon": "domain",
                        "link": reverse_lazy("admin:cadastro_despachante_changelist"),
                        "permission": lambda request: request.user.is_superuser,
                    },
                ],
            },
        ],
    },
}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'unique-snowflake',
    }
}

# ==============================================================================
# 10. E-MAIL (SMTP REAL)
# ==============================================================================
# Troca o backend de 'console' para 'smtp'
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL')