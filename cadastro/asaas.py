import requests
import json
from django.conf import settings

# --- CONFIGURAÇÃO ---
# Crie sua conta em: https://sandbox.asaas.com/
# Vá em Minha Conta > Integração > Chave de API
ASAAS_API_KEY = "$aact_hmlg_000MzkwODA2MWY2OGM3MWRlMDU2NWM3MzJlNzZmNGZhZGY6OmIzNmQxMWUzLTVmMmItNGM3Yy1hNTI3LThmNjg1ZjhkMDhiMTo6JGFhY2hfOWZmZmRiZmItNTYzMS00N2QwLTk3YTAtZWI1N2I3NTFlOTAx" # <--- COLE SUA CHAVE AQUI (começa com $aact)
ASAAS_URL = "https://sandbox.asaas.com/api/v3"

def headers():
    return {
        "Content-Type": "application/json",
        "access_token": ASAAS_API_KEY
    }

def criar_cliente_asaas(despachante):
    """
    Cria ou atualiza o cliente no Asaas com base nos dados do Despachante.
    Retorna o ID do cliente (cus_xxx) ou None se der erro.
    """
    url = f"{ASAAS_URL}/customers"
    
    # 1. Se já tem ID, retorna ele (evita duplicação)
    if despachante.asaas_customer_id:
        return despachante.asaas_customer_id

    # 2. Monta os dados para enviar
    # Tenta usar o email de fatura, se não tiver, usa o email principal
    email_envio = despachante.email_fatura or despachante.email or "sem_email@sistema.com"
    
    payload = {
        "name": despachante.nome_fantasia,
        "cpfCnpj": despachante.cnpj,
        "email": email_envio,
        "phone": despachante.telefone,
        "externalReference": str(despachante.id) # Ajuda a cruzar dados no futuro
    }

    # 3. Envia para o Asaas
    try:
        response = requests.post(url, json=payload, headers=headers())
        
        if response.status_code == 200:
            data = response.json()
            return data['id'] # Sucesso! Retorna algo tipo 'cus_00005...'
        else:
            print(f"Erro Asaas: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        print(f"Erro de conexão com Asaas: {e}")
        return None