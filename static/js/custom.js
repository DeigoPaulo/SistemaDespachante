document.addEventListener("DOMContentLoaded", function() {
    
    // =========================================================================
    // 1. MÁSCARAS (Aplicadas nos IDs do seu forms.py e classes genéricas)
    // =========================================================================
    if (typeof $ !== 'undefined' && $.fn.mask) {
        
        var cpfCnpjBehavior = function (val) {
            return val.replace(/\D/g, '').length === 11 ? '000.000.000-009' : '00.000.000/0000-00';
        };
        var cpfCnpjOptions = {
            onKeyPress: function (val, e, field, options) {
                field.mask(cpfCnpjBehavior.apply({}, arguments), options);
            }
        };
        $('#cpf_cnpj, #id_cpf_cnpj, .mask-cpf-cnpj').mask(cpfCnpjBehavior, cpfCnpjOptions);

        var placaBehavior = function (val) {
            var myMask = 'AAA0#00'; 
            var cleanVal = val.replace(/[^a-zA-Z0-9]/g, '');
            if (cleanVal.length > 4) {
                if (!isNaN(cleanVal.charAt(4))) { myMask = 'AAA-0000'; } 
                else { myMask = 'AAA0A00'; }
            }
            return myMask;
        };
        var placaOptions = {
            onKeyPress: function(val, e, field, options) {
                var oldVal = val;
                var newVal = oldVal.toUpperCase();
                if (oldVal !== newVal) { field.val(newVal); }
                field.mask(placaBehavior.apply({}, arguments), options);
            },
            'translation': { A: {pattern: /[A-Za-z]/}, 0: {pattern: /[0-9]/}, '#': {pattern: /[A-Za-z0-9]/} }
        };
        $('.mask-placa, #pop_placa').mask(placaBehavior, placaOptions);

        $('#telefone, #id_telefone, .mask-phone').mask('(00) 00000-0000');
        $('#cep, #id_cep, .mask-cep').mask('00000-000');
        $('.mask-year').mask('0000');
        $('.mask-number').mask('00000000000');
        $('.mask-money').mask('000.000.000,00', {reverse: true});
    }

    // =========================================================================
    // 2. UX: UPPERCASE E ALERTAS
    // =========================================================================
    setTimeout(function() {
        var alerts = document.querySelectorAll('.alert');
        alerts.forEach(function(alert) {
            if (typeof bootstrap !== 'undefined' && bootstrap.Alert) {
                new bootstrap.Alert(alert).close();
            } else { alert.style.display = 'none'; }
        });
    }, 5000);

    document.querySelectorAll('input[type="text"]').forEach(function(input) {
        if(!input.classList.contains('no-upper') && !input.classList.contains('mask-placa')) {
            input.addEventListener('input', function() {
                if(!this.name.includes('email') && !this.name.includes('senha')) {
                    this.value = this.value.toUpperCase();
                }
            });
        }
    });

    // =========================================================================
    // 3. CONSULTA BRASIL API (Baseada nos IDs do seu forms.py)
    // =========================================================================
    var inputDoc = document.getElementById('cpf_cnpj') || document.getElementById('id_cpf_cnpj');

    if (inputDoc) {
        inputDoc.addEventListener('blur', function() {
            var doc = this.value.replace(/\D/g, '');
            if(doc.length === 14) { 
                document.body.style.cursor = 'wait';
                fetch('https://brasilapi.com.br/api/cnpj/v1/' + doc)
                    .then(response => response.json())
                    .then(data => {
                        if(!data.message) {
                            setVal('nome', data.razao_social);
                            setVal('cep', data.cep);
                            setVal('rua', data.logradouro);
                            setVal('numero', data.numero);
                            setVal('bairro', data.bairro);
                            setVal('cidade', data.municipio);
                            setVal('uf', data.uf);
                            if(data.ddd_telefone_1) {
                                setVal('telefone', `(${data.ddd_telefone_1}) ${data.telefone_1}`);
                            }
                        }
                        document.body.style.cursor = 'default';
                    }).catch(() => document.body.style.cursor = 'default');
            }
        });
    }

    // =========================================================================
    // 4. CONSULTA VIA CEP
    // =========================================================================
    var cepInput = document.getElementById('cep') || document.getElementById('id_cep');

    if (cepInput) {
        cepInput.addEventListener('blur', function () {
            var cep = cepInput.value.replace(/\D/g, '');
            if (cep.length !== 8) return;

            fetch(`https://viacep.com.br/ws/${cep}/json/`)
                .then(response => response.json())
                .then(data => {
                    if (data.erro) return;
                    setVal('rua', data.logradouro);
                    setVal('bairro', data.bairro);
                    setVal('cidade', data.localidade);
                    setVal('uf', data.uf);
                })
                .catch(() => { console.warn('Erro ao buscar CEP'); });
        });
    }

    function setVal(id, value) {
        var el = document.getElementById(id); 
        if (!el) el = document.getElementById('id_' + id); 
        if (el) { el.value = value || ''; }
    }

    // =========================================================================
    // 5. SEGURANÇA
    // =========================================================================
    var confirmForms = document.querySelectorAll('.form-confirm');
    confirmForms.forEach(function(form) {
        form.addEventListener('submit', function(event) {
            var msg = this.getAttribute('data-msg') || "Confirma a operação?";
            if (!confirm(msg)) { event.preventDefault(); }
        });
    });

    // =========================================================================
    // 6. LÓGICA DO MODAL DE VEÍCULOS (Usado no Cadastro Rápido)
    // =========================================================================
    var btnSalvarModal = document.getElementById('btn-add-modal'); // Corrigido ID para btn-add-modal
    
    if (btnSalvarModal) {
        btnSalvarModal.addEventListener('click', function() {
            var placa = document.getElementById('pop_placa').value.toUpperCase();
            var renavam = document.getElementById('pop_renavam').value;
            var chassi = document.getElementById('pop_chassi') ? document.getElementById('pop_chassi').value.toUpperCase() : ''; // Verificação segura
            var marca = document.getElementById('pop_marca') ? document.getElementById('pop_marca').value.toUpperCase() : '';
            var modelo = document.getElementById('pop_modelo').value.toUpperCase();
            var cor = document.getElementById('pop_cor').value.toUpperCase();
            var anoFab = document.getElementById('pop_ano_fabricacao') ? document.getElementById('pop_ano_fabricacao').value : '';
            var anoMod = document.getElementById('pop_ano_modelo') ? document.getElementById('pop_ano_modelo').value : '';
            var anoUnico = document.getElementById('pop_ano') ? document.getElementById('pop_ano').value : ''; // Caso use um campo só
            var tipo = document.getElementById('pop_tipo') ? document.getElementById('pop_tipo').value : 'CARRO';
            var servico = document.getElementById('pop_servico') ? document.getElementById('pop_servico').value : '';
            var atendimento = document.getElementById('pop_atendimento') ? document.getElementById('pop_atendimento').value : '';

            // Usa o ano único se os separados estiverem vazios
            if(!anoFab && anoUnico) { anoFab = anoUnico; anoMod = anoUnico; }

            if (!placa || placa.length < 7) {
                var msg = document.getElementById('msg-erro-modal');
                if(msg) msg.classList.remove('d-none');
                return;
            }
            if(document.getElementById('msg-erro-modal')) document.getElementById('msg-erro-modal').classList.add('d-none');

            // --- Adicionar na Tabela Visual (Lógica reutilizável se a função existir) ---
            if (typeof adicionarNaTabela === "function") {
                // Se estiver no Cadastro Rápido que tem essa função no HTML
                adicionarNaTabela({
                    placa: placa,
                    renavam: renavam,
                    modelo: modelo,
                    cor: cor,
                    ano: anoFab,
                    servico: servico,
                    atendimento: atendimento
                });
            } else {
                // Lógica de fallback para outras telas (ex: Novo Cliente)
                var tbody = document.querySelector('#tabela-veiculos-visual tbody');
                var linhaVazia = document.getElementById('linha-vazia');
                if (linhaVazia) linhaVazia.remove();

                var tr = document.createElement('tr');
                tr.innerHTML = `
                    <td class="fw-bold text-primary">${placa}</td>
                    <td>${modelo}</td>
                    <td>${marca}</td>
                    <td>${cor}</td>
                    <td>${anoFab}/${anoMod}</td>
                    <td class="text-center">
                        <button type="button" class="btn btn-sm btn-danger btn-remove-item" title="Remover">
                            <i class="fas fa-trash"></i>
                        </button>
                    </td>
                `;
                if(tbody) tbody.appendChild(tr);

                var container = document.getElementById('veiculos-inputs-container');
                if(container) {
                    var divHidden = document.createElement('div');
                    divHidden.innerHTML = `
                        <input type="hidden" name="veiculo_placa[]" value="${placa}">
                        <input type="hidden" name="veiculo_renavam[]" value="${renavam}">
                        <input type="hidden" name="veiculo_chassi[]" value="${chassi}">
                        <input type="hidden" name="veiculo_marca[]" value="${marca}">
                        <input type="hidden" name="veiculo_modelo[]" value="${modelo}">
                        <input type="hidden" name="veiculo_cor[]" value="${cor}">
                        <input type="hidden" name="veiculo_ano[]" value="${anoFab}"> <input type="hidden" name="veiculo_ano_fabricacao[]" value="${anoFab}">
                        <input type="hidden" name="veiculo_ano_modelo[]" value="${anoMod}">
                        <input type="hidden" name="veiculo_tipo[]" value="${tipo}">
                    `;
                    container.appendChild(divHidden);

                    tr.querySelector('.btn-remove-item').onclick = function() {
                        tr.remove(); 
                        divHidden.remove(); 
                        if(tbody.children.length === 0) {
                            tbody.innerHTML = '<tr id="linha-vazia"><td colspan="6" class="text-center text-muted small py-2">Nenhum veículo adicionado.</td></tr>';
                        }
                    };
                }
            }

            // Limpa Campos
            document.querySelectorAll('#modalVeiculo input').forEach(i => i.value = '');
            
            var modalEl = document.getElementById('modalVeiculo');
            if (typeof bootstrap !== 'undefined' && modalEl) {
                var modalInstance = bootstrap.Modal.getInstance(modalEl);
                if (modalInstance) modalInstance.hide();
            }
        });
    }

    // =========================================================================
    // 7. AUTOCOMPLETE DE CLIENTE (Genérico para todas as telas)
    // =========================================================================
    setupClienteAutocomplete(); 

    

}); // --- FIM DO DOMContentLoaded ---


// =========================================================================
// FUNÇÕES GLOBAIS
// =========================================================================

// Filtro de Busca da Tabela Dashboard
const searchInput = document.getElementById('searchProcesso');
if (searchInput) {
    searchInput.addEventListener('keyup', function() {
        let value = this.value.toLowerCase();
        let rows = document.querySelectorAll('#tabelaProcessos tbody tr');
        rows.forEach(row => {
            if (!row.classList.contains('no-filter')) {
                row.style.display = row.innerText.toLowerCase().includes(value) ? '' : 'none';
            }
        });
    });
}

// FUNÇÃO REUTILIZÁVEL: Autocomplete de Cliente
function setupClienteAutocomplete() {
    const inputBusca = document.getElementById('busca_cliente') || document.getElementById('input-busca-cliente'); // Suporta os dois IDs usados
    const inputId = document.getElementById('cliente_id') || document.getElementById('input-cliente-id');
    const listaSugestoes = document.getElementById('lista_sugestoes') || document.getElementById('resultado-busca'); 
    
    // Elementos opcionais
    const btnLimpar = document.getElementById('btn_limpar_cliente');
    const msgConfirmado = document.getElementById('msg_cliente_confirmado');
    const infoCliente = document.getElementById('cliente-selecionado'); // Card verde do Cadastro Rápido
    const areaBusca = document.getElementById('area-busca');

    if (!inputBusca || !inputId) return;

    inputBusca.addEventListener('input', function() {
        let termo = this.value;
        if (termo.length < 3) { 
            if(listaSugestoes) listaSugestoes.style.display = 'none'; 
            return; 
        }

        fetch(`/api/buscar-clientes/?term=${termo}`)
            .then(r => r.json())
            .then(data => {
                if(listaSugestoes) {
                    listaSugestoes.innerHTML = '';
                    if (data.length > 0) {
                        listaSugestoes.style.display = 'block';
                        data.forEach(c => {
                            let item = document.createElement('a');
                            item.classList.add('list-group-item', 'list-group-item-action');
                            item.style.cursor = 'pointer';
                            item.innerHTML = `<strong>${c.nome}</strong> <small class="text-muted ms-2">${c.cpf}</small>`;
                            
                            item.onclick = function(e) {
                                e.preventDefault();
                                selecionarCliente(c);
                            };
                            listaSugestoes.appendChild(item);
                        });
                    } else {
                        listaSugestoes.style.display = 'none';
                    }
                }
            });
    });

    function selecionarCliente(c) {
        inputId.value = c.id;
        
        // Comportamento Orçamento: Preenche input e trava
        if(btnLimpar) {
            inputBusca.value = c.nome;
            inputBusca.setAttribute('readonly', true);
            inputBusca.classList.add('bg-white', 'text-success', 'fw-bold');
            btnLimpar.classList.remove('d-none');
            if(msgConfirmado) msgConfirmado.classList.remove('d-none');
        } 
        // Comportamento Cadastro Rápido: Esconde busca e mostra card
        else if (infoCliente && areaBusca) {
            areaBusca.style.display = 'none';
            infoCliente.classList.remove('d-none');
            document.getElementById('lbl-nome').innerText = c.nome;
            document.getElementById('lbl-cpf').innerText = c.cpf;
            if(document.getElementById('lbl-telefone')) document.getElementById('lbl-telefone').innerText = c.telefone;
            
            // Chama busca de veículos se a função existir (Cadastro Rápido)
            if (typeof buscarVeiculosDoCliente === "function") {
                buscarVeiculosDoCliente(c.id);
            }
        }

        if(listaSugestoes) listaSugestoes.style.display = 'none';
    }

    window.limparCliente = function() {
        inputId.value = '';
        inputBusca.value = '';
        inputBusca.removeAttribute('readonly');
        inputBusca.classList.remove('bg-white', 'text-success', 'fw-bold');
        
        if(btnLimpar) btnLimpar.classList.add('d-none');
        if(msgConfirmado) msgConfirmado.classList.add('d-none');
        
        inputBusca.focus();
    }

    // Botão "Trocar" do Cadastro Rápido
    const btnTrocar = document.getElementById('btn-trocar-cliente');
    if(btnTrocar) {
        btnTrocar.addEventListener('click', function() {
            inputId.value = '';
            infoCliente.classList.add('d-none');
            areaBusca.style.display = 'block';
            inputBusca.value = '';
            inputBusca.focus();
            if(listaSugestoes) listaSugestoes.innerHTML = '';
        });
    }

    document.addEventListener('click', function(e) {
        if (e.target !== inputBusca && listaSugestoes) {
            listaSugestoes.style.display = 'none';
        }
    });
}