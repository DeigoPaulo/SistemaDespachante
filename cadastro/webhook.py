import json
import logging
from datetime import timedelta
from django.utils import timezone
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from .models import Despachante

# Configura o logger para registrar erros no console/arquivo
logger = logging.getLogger(__name__)

@csrf_exempt # Permite que o Asaas envie dados sem token CSRF
def webhook_asaas(request):
    """
    Recebe notificações do Asaas sobre pagamentos.
    Se pago -> Renova a assinatura da Empresa (Despachante) e libera acesso.
    """
    if request.method != "POST":
        return JsonResponse({"status": "error", "msg": "Method not allowed"}, status=405)

    try:
        # --- 1. Ler os dados enviados pelo Asaas ---
        payload = json.loads(request.body)
        evento = payload.get('event')
        pagamento = payload.get('payment', {})
        
        customer_id = pagamento.get('customer')       # ID do Cliente no Asaas (cus_xxx)
        external_ref = pagamento.get('externalReference') # Nossa referência (mensalidade_ID_DATA)

        print(f"🔔 WEBHOOK RECEBIDO: {evento} | Cliente: {customer_id}")

        # --- 2. Processar Pagamento Confirmado ---
        if evento in ['PAYMENT_CONFIRMED', 'PAYMENT_RECEIVED']:
            
            # Tenta encontrar a empresa pelo ID do Asaas
            try:
                despachante = Despachante.objects.get(asaas_customer_id=customer_id)
            except Despachante.DoesNotExist:
                # Fallback: Tenta achar pelo ID na referência externa
                if external_ref and "mensalidade_" in external_ref:
                    try:
                        id_despachante = external_ref.split('_')[1]
                        despachante = Despachante.objects.get(id=id_despachante)
                    except:
                        logger.warning(f"Webhook ignorado: Despachante não encontrado. Ref: {external_ref}")
                        return JsonResponse({"status": "ignored", "msg": "Despachante não encontrado"})
                else:
                    return JsonResponse({"status": "ignored", "msg": "Cliente desconhecido"})

            # --- 3. A Mágica da Renovação (Ciclo Fixo) ---
            hoje = timezone.now().date()
            
            # Lógica: Sempre soma 30 dias na data existente para manter o dia do vencimento.
            # Se for a primeira vez (None), começa de hoje.
            
            if not despachante.data_validade_sistema:
                despachante.data_validade_sistema = hoje + timedelta(days=30)
                msg_tipo = "Primeira Ativação"
            else:
                # Se estava vencido ou adiantado, tanto faz: soma +30 no que estava lá.
                # Isso "cobra" os dias atrasados e mantém o ciclo alinhado.
                despachante.data_validade_sistema = despachante.data_validade_sistema + timedelta(days=30)
                msg_tipo = "Renovação de Ciclo (+30 dias)"
            
            # Garante que a empresa está ativa (caso tenha sido suspensa manualmente)
            if not despachante.ativo:
                despachante.ativo = True
                msg_tipo += " + Reativação"

            despachante.save()
            
            print(f"✅ SUCESSO: {despachante.nome_fantasia} -> Nova Validade: {despachante.data_validade_sistema} ({msg_tipo})")
            return JsonResponse({"status": "success", "msg": f"Renovado: {despachante.nome_fantasia}"})

        # --- 4. Processar Cobrança Vencida (Opcional) ---
        elif evento == 'PAYMENT_OVERDUE':
            # O sistema já bloqueia automaticamente pela data, então apenas logamos.
            print(f"⚠️ AVISO: Pagamento vencido para {customer_id}")
            pass 

        return JsonResponse({"status": "received"})

    except Exception as e:
        logger.error(f"Erro Crítico no Webhook: {str(e)}")
        return JsonResponse({"status": "error", "msg": str(e)}, status=500)