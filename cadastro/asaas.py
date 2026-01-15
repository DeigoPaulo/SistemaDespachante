import requests
import re
from datetime import date, timedelta
from django.conf import settings

# --- SUAS CHAVES ---
# Chave mantida exatamente como você enviou
ASAAS_API_KEY = "$aact_hmlg_000MzkwODA2MWY2OGM3MWRlMDU2NWM3MzJlNzZmNGZhZGY6OmIwOGJiZmFjLWZhOGYtNDMxZC1hNDA3LTQ4NjIxZDEwMWFmNjo6JGFhY2hfZDBiMzE2MDgtYTFjNy00NDdjLTllMjItOWNmMThjNzEwMjAz"
ASAAS_URL = "https://sandbox.asaas.com/api/v3"

def headers():
    return {
        "Content-Type": "application/json",
        "access_token": ASAAS_API_KEY
    }

def criar_cliente_asaas(despachante):
    """
    Cria ou recupera o cliente no Asaas.
    Retorna o ID (cus_xxx) ou None se der erro.
    """
    # 1. Se já tem ID salvo no banco, retorna ele
    if despachante.asaas_customer_id:
        return despachante.asaas_customer_id

    # 2. Limpeza de dados (Remove pontos e traços do CNPJ)
    cpf_limpo = re.sub(r'[^0-9]', '', despachante.cnpj)

    # 3. Lógica do Nome: Prioriza Razão Social. Se não tiver, usa Nome Fantasia.
    nome_final = despachante.razao_social if despachante.razao_social else despachante.nome_fantasia

    url = f"{ASAAS_URL}/customers"
    
    payload = {
        "name": nome_final, # <-- Atualizado para usar o nome correto
        "cpfCnpj": cpf_limpo,
        "email": despachante.email_fatura or despachante.email or "email@teste.com",
        "mobilePhone": despachante.telefone,
        
        # --- NOVOS CAMPOS DE ENDEREÇO ---
        # Enviamos o endereço completo no campo principal
        "address": despachante.endereco_completo,
        "addressNumber": "S/N", # Obrigatório na API
        "postalCode": "74000-000", # CEP Genérico (Goiânia) para validar
        
        "externalReference": str(despachante.id)
    }

    try:
        response = requests.post(url, json=payload, headers=headers())
        
        if response.status_code == 200:
            data = response.json()
            
            # SALVA NO BANCO
            despachante.asaas_customer_id = data['id']
            despachante.save()
            
            return data['id']
        else:
            print(f"❌ Erro Asaas (Criar Cliente): {response.text}")
            return None

    except Exception as e:
        print(f"❌ Erro de conexão: {e}")
        return None

def gerar_boleto_asaas(despachante, dias_para_vencimento=3):
    """
    Gera o boleto e retorna os links.
    """
    # 1. Tenta obter ou criar o cliente
    customer_id = criar_cliente_asaas(despachante)

    # 2. TRAVA DE SEGURANÇA
    if not customer_id:
        return {"sucesso": False, "erro": "Não foi possível cadastrar o cliente (Verifique CNPJ válido)"}

    # 3. Calcula vencimento e monta boleto
    data_vencimento = date.today() + timedelta(days=dias_para_vencimento)

    payload = {
        "customer": customer_id,
        "billingType": "BOLETO",
        "value": float(despachante.valor_mensalidade),
        "dueDate": data_vencimento.strftime("%Y-%m-%d"),
        "description": f"Mensalidade Sistema - Venc: {data_vencimento.strftime('%d/%m/%Y')}",
        "postalService": False
    }

    try:
        response = requests.post(f"{ASAAS_URL}/payments", json=payload, headers=headers())
        
        if response.status_code == 200:
            data = response.json()
            return {
                "sucesso": True,
                "link_boleto": data['bankSlipUrl'],
                "link_fatura": data['invoiceUrl'],
                "id": data['id']
            }
        else:
            return {"sucesso": False, "erro": f"Asaas rejeitou cobrança: {response.text}"}
            
    except Exception as e:
        return {"sucesso": False, "erro": str(e)}