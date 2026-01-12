
// =========================================================================
    // 8. CÁLCULO FINANCEIRO (Correção da Vírgula)
    // =========================================================================
    function converterDinheiro(valorString) {
        if (!valorString) return 0.0;
        // 1. Remove tudo que não é número ou vírgula (tira o ponto de milhar se tiver)
        // Ex: "1.200,50" vira "1200,50"
        var limpo = valorString.replace(/\./g, '').replace(/[^\d,]/g, '');
        // 2. Troca vírgula por ponto para o JS entender
        // Ex: "1200,50" vira "1200.50"
        return parseFloat(limpo.replace(',', '.'));
    }

    var inputHonorarios = $('#id_honorarios');
    var inputImpostos = $('#id_impostos');

    if (inputHonorarios.length > 0 && inputImpostos.length > 0) {
        inputHonorarios.on('input', function() {
            var valorDigitado = $(this).val();
            var valorFloat = converterDinheiro(valorDigitado);
            
            // Alíquota de 6% (0.06)
            var imposto = valorFloat * 0.06;

            // Formata de volta para o padrão brasileiro (vírgula)
            // toFixed(2) garante 2 casas decimais
            var valorFinal = imposto.toFixed(2).replace('.', ',');

            inputImpostos.val(valorFinal).trigger('input'); // Trigger aciona máscaras se existirem
            
            console.log("Cálculo Corrigido -> Honorários:", valorFloat, "| Imposto:", imposto);
        });
    }