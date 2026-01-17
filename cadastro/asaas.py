import os
import requests
import re
import calendar
from datetime import date, timedelta
from dotenv import load_dotenv

# ==============================================================================
# CONFIGURAÇÃO DE AMBIENTE
# ==============================================================================

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env_path = os.path.join(BASE_DIR, '.env')
load_dotenv(env_path)

ASAAS_API_KEY = os.getenv("ASAAS_API_KEY")
ASAAS_URL = "https://sandbox.asaas.com/api/v3" # Troque para 'api.asaas.com' em produção

if not ASAAS_API_KEY:
    print("⚠️ AVISO: Chave ASAAS_API_KEY não encontrada no arquivo .env")

# ==============================================================================
# FUNÇÕES AUXILIARES
# ==============================================================================

def calcular_data_vencimento_preferencial(dia_preferencial):
    """
    Calcula a próxima data válida com base no dia preferencial escolhido (1-28).
    Se o dia já passou neste mês, joga para o próximo.
    """
    hoje = date.today()
    try:
        dia = int(dia_preferencial)
    except:
        return hoje + timedelta(days=3) # Fallback se não tiver dia configurado

    # 1. Tenta criar a data neste mês
    try:
        # Verifica último dia do mês atual (pra não quebrar dia 31 em mês de 30)
        ultimo_dia_mes = calendar.monthrange(hoje.year, hoje.month)[1]
        dia_ajustado = min(dia, ultimo_dia_mes)
        vencimento_este_mes = date(hoje.year, hoje.month, dia_ajustado)
    except ValueError:
        vencimento_este_mes = hoje # Segurança

    # 2. Se a data deste mês for Hoje ou Futuro, usa ela
    if vencimento_este_mes >= hoje:
        return vencimento_este_mes
    
    # 3. Se já passou (ex: hoje é 15 e o dia é 10), joga para o MÊS QUE VEM
    else:
        prox_mes = hoje.month + 1
        ano = hoje.year
        if prox_mes > 12:
            prox_mes = 1
            ano += 1
        
        ultimo_dia_prox_mes = calendar.monthrange(ano, prox_mes)[1]
        dia_ajustado = min(dia, ultimo_dia_prox_mes)
        return date(ano, prox_mes, dia_ajustado)

# ==============================================================================
# FUNÇÕES DO ASAAS
# ==============================================================================

def headers():
    return {
        "Content-Type": "application/json",
        "access_token": ASAAS_API_KEY
    }

def criar_cliente_asaas(despachante):
    if despachante.asaas_customer_id:
        return despachante.asaas_customer_id

    cnpj_raw = despachante.cnpj if despachante.cnpj else ""
    cpf_limpo = re.sub(r'[^0-9]', '', cnpj_raw)
    nome_final = despachante.razao_social if despachante.razao_social else despachante.nome_fantasia

    url = f"{ASAAS_URL}/customers"
    
    payload = {
        "name": nome_final, 
        "cpfCnpj": cpf_limpo,
        "email": despachante.email_fatura or despachante.email or "email@teste.com",
        "mobilePhone": despachante.telefone,
        "address": despachante.endereco_completo,
        "addressNumber": "S/N",
        "postalCode": "74000-000",
        "externalReference": str(despachante.id)
    }

    try:
        response = requests.post(url, json=payload, headers=headers())
        
        if response.status_code == 200:
            data = response.json()
            despachante.asaas_customer_id = data['id']
            despachante.save()
            return data['id']
            
        elif response.status_code == 400 and "exists" in response.text:
            # Lógica de recuperação
            busca = requests.get(f"{ASAAS_URL}/customers?cpfCnpj={cpf_limpo}", headers=headers())
            if busca.status_code == 200:
                dados_busca = busca.json()
                if dados_busca.get('data'):
                    cliente_recuperado = dados_busca['data'][0]
                    despachante.asaas_customer_id = cliente_recuperado['id']
                    despachante.save()
                    return cliente_recuperado['id']
        else:
            print(f"❌ Erro Asaas (Criar Cliente): {response.text}")
            return None

    except Exception as e:
        print(f"❌ Erro de conexão ao criar cliente: {e}")
        return None

def buscar_fatura_pendente(customer_id):
    url = f"{ASAAS_URL}/payments"
    params = {
        "customer": customer_id,
        "status": "PENDING,OVERDUE", 
        "limit": 1
    }
    try:
        response = requests.get(url, headers=headers(), params=params)
        if response.status_code == 200:
            dados = response.json()
            if dados.get('data'):
                fatura = dados['data'][0]
                return {
                    "sucesso": True,
                    "link_fatura": fatura['invoiceUrl'],
                    "link_boleto": fatura['bankSlipUrl'],
                    "id": fatura['id'],
                    "msg": "Fatura existente recuperada"
                }
    except Exception as e:
        print(f"Erro ao buscar pendente: {e}")
    return None

def gerar_boleto_asaas(despachante, dias_para_vencimento=None):
    """
    Gera boleto respeitando o Dia de Vencimento do cadastro.
    Se 'dias_para_vencimento' for passado, ele sobrescreve a lógica do cadastro (usado pelo Robô).
    """
    if not ASAAS_API_KEY:
         return {"sucesso": False, "erro": "Chave API Asaas não configurada"}

    customer_id = criar_cliente_asaas(despachante)
    if not customer_id:
        return {"sucesso": False, "erro": "Não foi possível cadastrar o cliente (Verifique CNPJ válido)"}

    # Verifica se já tem boleto
    fatura_existente = buscar_fatura_pendente(customer_id)
    if fatura_existente:
        return fatura_existente

    # --- DEFINIÇÃO DA DATA DE VENCIMENTO ---
    if dias_para_vencimento:
        # Se o Robô mandou uma data específica (ex: daqui 5 dias)
        data_vencimento = date.today() + timedelta(days=dias_para_vencimento)
    else:
        # Lógica Padrão: Respeita o cadastro do Despachante
        if despachante.dia_vencimento:
            data_vencimento = calcular_data_vencimento_preferencial(despachante.dia_vencimento)
        else:
            # Se não configurou dia, joga para +3 dias
            data_vencimento = date.today() + timedelta(days=3)

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