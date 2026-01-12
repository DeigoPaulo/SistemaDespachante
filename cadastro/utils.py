# cadastro/utils.py

from .models import LogAtividade
import io
import fitz  # PyMuPDF
import gc    # Garbage Collection (Coletor de lixo da memória)

# ==============================================================================
# 1. FUNÇÃO DE LOGS (AUDITORIA)
# ==============================================================================
def registrar_log(request, acao, descricao, atendimento=None, cliente=None):
    """
    Registra uma atividade no sistema de forma padronizada.
    """
    try:
        # Verifica se o usuário tem perfil antes de tentar acessar
        if hasattr(request.user, 'perfilusuario'):
            despachante = request.user.perfilusuario.despachante
            
            LogAtividade.objects.create(
                despachante=despachante,
                usuario=request.user,
                acao=acao,
                descricao=descricao,
                atendimento=atendimento,
                cliente=cliente
            )
    except Exception as e:
        # Se der erro no log, apenas printa para não travar o sistema principal
        print(f"Erro ao gravar log: {e}")

# ==============================================================================
# 2. FUNÇÃO DE COMPRESSÃO DE PDF (OTIMIZADA)
# ==============================================================================
def comprimir_pdf_memoria(arquivo_upload):
    """
    Reconstrói o PDF transformando páginas em imagens otimizadas (JPEG).
    Ideal para "esmagar" arquivos pesados (ex: Scans de alta resolução).
    """
    try:
        # Lê o arquivo da memória
        input_bytes = arquivo_upload.read()
        
        # Abre o PDF original
        doc_original = fitz.open(stream=input_bytes, filetype="pdf")
        
        # Cria um novo PDF vazio
        doc_novo = fitz.open()

        for pagina in doc_original:
            # 1. Renderiza a página como imagem (Pixmap)
            # Matrix(1.5, 1.5) ≈ 110 DPI. 
            # É o "ponto ideal": legível na tela e leve para upload no Detran.
            pix = pagina.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            
            # 2. Converte para JPEG com compressão
            # jpg_quality=70 é o padrão da indústria para documentos (legível e leve)
            imagem_bytes = pix.tobytes("jpg", jpg_quality=70)
            
            # 3. Cria página no novo PDF com as dimensões originais
            nova_pagina = doc_novo.new_page(width=pagina.rect.width, height=pagina.rect.height)
            
            # 4. Insere a imagem otimizada preenchendo a página toda
            nova_pagina.insert_image(nova_pagina.rect, stream=imagem_bytes)

            # 5. Limpeza de memória imediata (Crucial para PDFs com muitas páginas)
            del pix
            del imagem_bytes

        # Salva o novo PDF no buffer de memória
        buffer_saida = io.BytesIO()
        
        # garbage=4: Remove objetos duplicados e não utilizados (compressão máxima da estrutura)
        # deflate=True: Comprime as streams de dados
        doc_novo.save(buffer_saida, deflate=True, garbage=4)
        
        buffer_saida.seek(0)
        
        # Fecha documentos e força limpeza da memória do servidor
        doc_original.close()
        doc_novo.close()
        gc.collect() 

        return buffer_saida

    except Exception as e:
        print(f"Erro crítico na compressão do PDF: {e}")
        # Retorna None para que a View saiba que falhou e avise o usuário
        return None