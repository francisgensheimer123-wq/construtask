document.addEventListener("DOMContentLoaded", function () {

    const contratoField = document.getElementById("id_contrato");

    if (!contratoField) return;

    contratoField.addEventListener("change", function () {

        const contratoId = this.value;

        if (!contratoId) return;

        fetch(`/admin/Construtask/medicao/buscar-contrato/${contratoId}/`)
            .then(response => response.json())
            .then(data => {

                document.getElementById("id_fornecedor").value = data.fornecedor || "";
                document.getElementById("id_cnpj").value = data.cnpj || "";
                document.getElementById("id_responsavel").value = data.responsavel || "";
                document.getElementById("id_valor_contrato").value = data.valor_contrato || "";

            });
    });

});