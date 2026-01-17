import requests
import json

# --- CONFIGURAÇÕES ---
# URL do seu Webhook local
URL_WEBHOOK = "http://127.0.0.1:8000/api/webhook/asaas/"

# O ID do cliente que você quer "baixar" o pagamento
# (Tem que ser IGUAL ao que está no campo "ID Asaas" no cadastro do Despachante)
ID_CLIENTE_SIMULADO = "cus_000007460953" 

payload = {
    "event": "PAYMENT_CONFIRMED",
    "payment": {
        "customer": ID_CLIENTE_SIMULADO,
        "billingType": "BOLETO",
        "value": 150.00,
        "netValue": 145.00,
        "dateCreated": "2026-01-15",
        "billingType": "BOLETO",
        # Referência opcional, mas ajuda o sistema a achar caso o ID falhe
        "externalReference": "mensalidade_teste_manual" 
    }
}

print(f"📡 Disparando webhook para: {URL_WEBHOOK}")
print(f"👤 Cliente Asaas: {ID_CLIENTE_SIMULADO}")

try:
    response = requests.post(URL_WEBHOOK, json=payload)
    
    print("\n" + "="*40)
    print(f"STATUS HTTP: {response.status_code}")
    print(f"RESPOSTA DO SISTEMA: {response.text}")
    print("="*40 + "\n")

    if response.status_code == 200:
        print("✅ SUCESSO! O sistema aceitou a baixa.")
        print("👉 Verifique no Admin se a data de validade avançou 30 dias.")
    else:
        print("❌ FALHA! Verifique o terminal do Django para ver o erro.")

except Exception as e:
    print(f"❌ Erro de conexão: {e}")
    print("Dica: Verifique se o servidor (runserver) está rodando.")