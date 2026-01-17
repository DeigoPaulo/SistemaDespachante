from django.core.management.base import BaseCommand
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from datetime import timedelta
from cadastro.models import Despachante
from cadastro.asaas import gerar_boleto_asaas

class Command(BaseCommand):
    help = 'Gera cobranças automáticas baseadas no vencimento do sistema'

    def handle(self, *args, **kwargs):
        self.stdout.write("🤖 Iniciando robô de cobrança...")
        
        hoje = timezone.now().date()
        
        # Define com quantos dias de antecedência o robô deve agir
        # Ex: Se vence dia 20, e hoje é dia 10, ele gera.
        dias_antecedencia = 10
        data_alvo = hoje + timedelta(days=dias_antecedencia)
        
        # Filtra despachantes ativos que vencem exatamente na data alvo
        alvos = Despachante.objects.filter(
            ativo=True, 
            data_validade_sistema=data_alvo
        )
        
        if not alvos.exists():
            self.stdout.write(f"💤 Ninguém vence no dia {data_alvo.strftime('%d/%m/%Y')}. Nada a fazer.")
            return

        self.stdout.write(f"🔎 Encontrados {alvos.count()} clientes com sistema vencendo em {data_alvo.strftime('%d/%m/%Y')}.")

        for despachante in alvos:
            self.stdout.write(f"   > Processando: {despachante.nome_fantasia}...")
            
            # --- CORREÇÃO AQUI ---
            # Chamamos SEM passar dias. Assim ele usa o 'dia_vencimento' do cadastro.
            resultado = gerar_boleto_asaas(despachante) 
            
            if resultado['sucesso']:
                link_fatura = resultado['link_fatura']
                
                # Envia E-mail
                assunto = f"Fatura de Renovação - {despachante.nome_fantasia}"
                mensagem = f"""
                Olá, {despachante.nome_fantasia}!
                
                Seu acesso ao sistema vence em breve ({data_alvo.strftime('%d/%m/%Y')}).
                
                Para manter seu acesso ininterrupto, geramos sua fatura conforme seu dia de vencimento preferencial.
                
                💰 Valor: R$ {despachante.valor_mensalidade}
                📄 Boleto/Pix: {link_fatura}
                
                O pagamento será baixado automaticamente e renovará seu ciclo por mais 30 dias.
                """
                
                try:
                    email_destino = despachante.email_fatura or despachante.email
                    if email_destino:
                        send_mail(
                            assunto, 
                            mensagem, 
                            settings.DEFAULT_FROM_EMAIL or 'financeiro@seusistema.com.br', 
                            [email_destino], 
                            fail_silently=False
                        )
                        self.stdout.write(self.style.SUCCESS(f"     ✅ Cobrança enviada para {email_destino}"))
                    else:
                        self.stdout.write(self.style.WARNING(f"     ⚠️ Boleto gerado, mas cliente sem e-mail cadastrado."))
                        
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"     ❌ Erro ao enviar e-mail: {e}"))
            else:
                self.stdout.write(self.style.ERROR(f"     ❌ Erro no Asaas: {resultado.get('erro')}"))

        self.stdout.write("🤖 Fim da execução.")