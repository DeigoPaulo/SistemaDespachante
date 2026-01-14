# 1. Usa uma imagem oficial do Python (leve e segura)
FROM python:3.12-slim

# 2. Define variáveis de ambiente para otimizar o Python no Docker
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# 3. Define a pasta de trabalho dentro do container
WORKDIR /app

# 4. ATUALIZADO: Instala dependências do sistema para compilação
# build-essential e python3-dev: Para compilar CFFI e Gevent
# libjpeg-dev e zlib1g-dev: Para o Pillow processar imagens
# libpq-dev: Para o banco de dados Postgres
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    libjpeg-dev \
    zlib1g-dev \
    libffi-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# 5. Copia o arquivo de requisitos e instala as bibliotecas Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 6. Copia todo o código do seu projeto para dentro do container
COPY . .

# 7. Expõe a porta 8000 (onde o Django roda)
EXPOSE 8000

# 8. Comando para iniciar o servidor (Usando Gunicorn)
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]