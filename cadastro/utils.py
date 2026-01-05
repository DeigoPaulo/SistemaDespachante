import io
import fitz  # PyMuPDF

def comprimir_pdf_memoria(arquivo_upload):
    """
    Estratégia "Reconstrução":
    1. Renderiza cada página do PDF como uma imagem.
    2. Salva essa imagem com compressão JPEG (qualidade 70).
    3. Cria um NOVO PDF contendo apenas essas imagens otimizadas.
    """
    try:
        # Lê o arquivo da memória
        input_bytes = arquivo_upload.read()
        
        # Abre o PDF original
        doc_original = fitz.open(stream=input_bytes, filetype="pdf")
        
        # Cria um novo PDF vazio (que será o leve)
        doc_novo = fitz.open()

        for pagina in doc_original:
            # 1. Renderiza a página original como imagem (Pixmap)
            # Matrix(1.8, 1.8) = 150 DPI (boa leitura, arquivo leve)
            # Se quiser MENOR, use Matrix(1.0, 1.0)
            pix = pagina.get_pixmap(matrix=fitz.Matrix(1.8, 1.8), alpha=False)
            
            # 2. Converte para bytes de imagem JPEG com compressão
            # A CORREÇÃO ESTÁ AQUI: O nome certo é 'jpg_quality'
            imagem_bytes = pix.tobytes("jpg", jpg_quality=72)
            
            # 3. Cria uma página nova no PDF novo com o tamanho da original
            nova_pagina = doc_novo.new_page(width=pagina.rect.width, height=pagina.rect.height)
            
            # 4. Insere a imagem otimizada preenchendo a página toda
            nova_pagina.insert_image(nova_pagina.rect, stream=imagem_bytes)

        # Salva o novo PDF no buffer de memória
        buffer_saida = io.BytesIO()
        
        # Salva comprimindo o container PDF também
        doc_novo.save(buffer_saida, deflate=True)
        
        buffer_saida.seek(0)
        
        # Fecha os documentos
        doc_original.close()
        doc_novo.close()

        return buffer_saida

    except Exception as e:
        print(f"Erro na reconstrução do PDF: {e}")
        return None