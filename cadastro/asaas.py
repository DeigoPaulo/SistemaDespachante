import os
import requests
import re
from datetime import date, timedelta
from django.conf import settings
from dotenv import load_dotenv

# ==============================================================================
# CONFIGURAÇÃO DE AMBIENTE (Correção para evitar erro de coroutine)
# ==============================================================================

# Usamos os.path.abspath para garantir o caminho real e síncrono
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env_path = os.path.join(BASE_DIR, '.env')

# Carrega o arquivo .env
load_dotenv(env_path)

# --- SUAS CHAVES ---
ASAAS_API_KEY = os.getenv("ASAAS_API_KEY")
ASAAS_URL = "https://sandbox.asaas.com/api/v3"

# Se não achar a chave, avisa no terminal (ajuda no debug)
if not ASAAS_API_KEY:
    print("⚠️ AVISO: Chave ASAAS_API_KEY não encontrada no arquivo .env")

# ==============================================================================
# FUNÇÕES DO ASAAS
# ==============================================================================

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
    # Garante que cnpj não seja None para evitar erro no re.sub
    cnpj_raw = despachante.cnpj if despachante.cnpj else ""
    cpf_limpo = re.sub(r'[^0-9]', '', cnpj_raw)

    # 3. Lógica do Nome: Prioriza Razão Social. Se não tiver, usa Nome Fantasia.
    nome_final = despachante.razao_social if despachante.razao_social else despachante.nome_fantasia

    url = f"{ASAAS_URL}/customers"
    
    payload = {
        "name": nome_final, 
        "cpfCnpj": cpf_limpo,
        "email": despachante.email_fatura or despachante.email or "email@teste.com",
        "mobilePhone": despachante.telefone,
        
        # --- CAMPOS DE ENDEREÇO ---
        "address": despachante.endereco_completo,
        "addressNumber": "S/N", # Obrigatório na API
        "postalCode": "74000-000", # CEP Genérico se não tiver
        
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
            
        # Tratamento especial: Cliente já existe no Asaas (Erro 400)
        elif response.status_code == 400 and "already exists" in response.text:
            print("ℹ️ Cliente já existe no Asaas. Buscando ID...")
            busca = requests.get(f"{ASAAS_URL}/customers?cpfCnpj={cpf_limpo}", headers=headers())
            if busca.status_code == 200:
                dados_busca = busca.json()
                if dados_busca['data']:
                    cliente_recuperado = dados_busca['data'][0]
                    # Salva o ID recuperado para não precisar buscar de novo
                    despachante.asaas_customer_id = cliente_recuperado['id']
                    despachante.save()
                    return cliente_recuperado['id']

        else:
            print(f"❌ Erro Asaas (Criar Cliente): {response.text}")
            return None

    except Exception as e:
        print(f"❌ Erro de conexão ao criar cliente: {e}")
        return None

def gerar_boleto_asaas(despachante, dias_para_vencimento=3):
    """
    Gera o boleto e retorna os links.
    """
    if not ASAAS_API_KEY:
         return {"sucesso": False, "erro": "Chave API Asaas não configurada"}

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
        "postalService": False,
        "externalReference": f"mensalidade_{despachante.id}_{date.today().strftime('%m%Y')}"
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