"""Mede o footprint real (flash/RAM) do Random Forest treinado
(`models/activity_classifier_rf.joblib`) convertido para C via `emlearn`, e
mede o impacto real na accuracy da quantização usada por omissão nesse
caminho (`int16_t`) — ver "Resultado real" no final desta docstring, é uma
queda grande, não confirmação de que está tudo bem.

Contexto (ver ml/README.md e PROJECT_STATUS.md, "Estudo de viabilidade
TinyML"): o footprint do XGBoost/Random Forest nunca tinha sido medido de
facto nesta placa — só havia um precedente publicado de terceiros (500
árvores a exigirem 553-727KB de flash) usado como estimativa indireta. Este
script mede o número real para o NOSSO modelo (80 árvores, profundidade 5),
compilando o código C gerado pelo `emlearn` com o toolchain ARM
(`arm-none-eabi-gcc`, mesmo alvo do firmware: Cortex-M4F do nRF52840) e lendo
o tamanho das secções resultantes — não é matemática de papel.

O `emlearn` suporta dois métodos de geração de código, com trade-off
diferente:
  - "inline": cada árvore vira uma função com if/else aninhados — código
    denso, mais rápido, mas sem partilha de dados entre árvores (maior
    flash).
  - "loadable": os nós de todas as árvores ficam numa tabela de dados
    (`static const`), percorrida por uma função genérica única — menos
    código gerado, mais compacto em flash, ligeiramente mais lento por nó
    visitado (indireção de tabela em vez de branch direto).
Este script mede os dois, para a decisão de qual usar (se se avançar para
embarcar) ser feita com números reais dos dois lados, não só teoria.

IMPORTANTE (consistente com a regra do projeto de nunca fabricar
resultados): se o toolchain ARM (`arm-none-eabi-gcc`) não estiver instalado,
o script falha com um erro claro em vez de simular/inventar um número.

Para reproduzir:
    pip install -r requirements.txt   # inclui emlearn e joblib
    sudo apt-get install -y gcc-arm-none-eabi   # ou equivalente da distro
    python synthetic_data.py          # se data/*.csv ainda não existir
    python train_activity_classifier_rf.py   # se o .joblib ainda não existir
    python measure_rf_footprint.py

Resultado real (2026-07-03, ver ml/README.md para o detalhe completo): a
quantização int16 por omissão do `emlearn` reduz a accuracy medida de 0.978
(sklearn) para ~0.79 — queda grande, não uma confirmação de viabilidade.
Manter os limiares em `float` ('inline', dtype='float') recupera a accuracy
original, a troco de mais flash (~19KB em vez de ~5-10KB). Ver `reports/
activity_classifier_rf_footprint.json` para os três números lado a lado.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path

import numpy as np

MODEL_PATH = "models/activity_classifier_rf.joblib"
OUTPUT_REPORT = "reports/activity_classifier_rf_footprint.json"

# Alvo do firmware real (ver platformio.ini / PROJECT_STATUS.md): Cortex-M4F
# do nRF52840, com FPU de precisão simples.
ARM_CFLAGS = [
    "-mcpu=cortex-m4", "-mthumb", "-mfpu=fpv4-sp-d16", "-mfloat-abi=hard",
    "-Os", "-ffunction-sections", "-fdata-sections",
]

# Orçamento livre desta placa com o resto do firmware já a correr (ver
# PROJECT_STATUS.md, "Estudo de viabilidade TinyML") — usado só para
# contextualizar o resultado no relatório, não é um limite imposto pelo
# script.
FREE_FLASH_BYTES_BUDGET = 638 * 1024
FREE_RAM_BYTES_BUDGET = 220 * 1024


def require_arm_toolchain():
    if shutil.which("arm-none-eabi-gcc") is None:
        sys.exit(
            "ERRO: 'arm-none-eabi-gcc' nao encontrado no PATH. Este script "
            "mede footprint real compilando para o alvo do firmware "
            "(Cortex-M4F) - nao inventa um numero sem o toolchain. Instalar "
            "com, por exemplo, 'sudo apt-get install -y gcc-arm-none-eabi'."
        )


def generate_c_code(model, method, dtype, name, workdir):
    """Converte o modelo treinado para C via emlearn e devolve o caminho do
    ficheiro gerado. 'method' e' 'inline' ou 'loadable'; 'dtype' e' o tipo
    usado para features/limiares ('int16_t' ou 'float' — 'loadable' só
    suporta 'int16_t', ver docstring do módulo)."""
    import emlearn

    wrapper = emlearn.convert(model, method=method, dtype=dtype)
    out_path = workdir / f"{name}.h"
    wrapper.save(name=name, file=str(out_path), format="c")
    return wrapper, out_path


def compile_and_measure(header_path, name, workdir):
    """Compila um ficheiro C mínimo (só inclui o cabeçalho gerado — a
    função de previsão já tem ligação externa, não precisa de um wrapper
    que a chame para ficar no objeto) e devolve o tamanho (bytes) de cada
    secção ELF relevante para footprint embarcado: 'flash' (.text +
    .rodata + .data, o que fica gravado na flash) e 'ram' (.data + .bss, o
    que ocupa RAM em runtime)."""
    import emlearn

    main_c = workdir / f"main_{name}.c"
    main_c.write_text(f'#include <stdint.h>\n#include "{header_path.name}"\n')
    obj_path = workdir / f"{name}.o"
    cmd = [
        "arm-none-eabi-gcc", *ARM_CFLAGS,
        "-I", str(Path(emlearn.includedir)),
        "-c", str(main_c), "-o", str(obj_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"falha a compilar {name}: {result.stderr}")

    size_out = subprocess.run(
        ["arm-none-eabi-size", "--format=sysv", str(obj_path)],
        capture_output=True, text=True, check=True,
    ).stdout

    # Formato sysv lista uma linha por secção ELF ("nome  tamanho  endereco").
    # Somamos por categoria: .text*/.rodata* -> flash; .data*/.bss* -> ram
    # (.data conta para os dois: fica gravado na flash como valor inicial
    # E ocupa RAM em runtime).
    flash_bytes = 0
    ram_bytes = 0
    for line in size_out.splitlines():
        parts = line.split()
        if len(parts) < 2 or not parts[1].isdigit():
            continue
        section, size = parts[0], int(parts[1])
        if section.startswith(".text") or section.startswith(".rodata"):
            flash_bytes += size
        elif section.startswith(".data"):
            flash_bytes += size
            ram_bytes += size
        elif section.startswith(".bss"):
            ram_bytes += size

    return {"flash_bytes": flash_bytes, "ram_bytes": ram_bytes}


def measure_c_accuracy(model, method, dtype, X_test, y_test_labels, class_names):
    """Compara a accuracy do modelo original (sklearn, float64) com a do
    mesmo modelo depois de convertido para o caminho C usado no MCU. O
    'wrapper.predict()' do emlearn compila e corre o MESMO código C gerado
    para o firmware, via um binário nativo (host) - não é uma simulação em
    Python, é o comportamento real (incluindo a quantização, se dtype for
    'int16_t') a correr."""
    import emlearn

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wrapper = emlearn.convert(model, method=method, dtype=dtype)
        y_pred_idx = wrapper.predict(X_test.to_numpy(dtype="float32"))

    y_pred_labels = [class_names[i] for i in y_pred_idx]
    accuracy = float(np.mean(np.array(y_pred_labels) == np.array(y_test_labels)))
    return accuracy


def main():
    require_arm_toolchain()

    import joblib
    sys.path.insert(0, str(Path(__file__).parent))
    from train_activity_classifier import load_dataset, split_by_subject

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # aviso de versao do sklearn no unpickle - inofensivo aqui
        model = joblib.load(MODEL_PATH)

    df, feature_cols = load_dataset()
    _, test_df, _test_subjects = split_by_subject(df)
    X_test = test_df[feature_cols]
    y_test_labels = test_df["label"].tolist()
    # O modelo foi treinado sobre y codificado por LabelEncoder (ver
    # train_activity_classifier_rf.py); as classes internas do modelo sao
    # inteiros (0..4), nao os nomes. Reconstroi o mesmo mapeamento a partir
    # do dataset para traduzir de volta para os nomes de classe.
    from sklearn.preprocessing import LabelEncoder
    encoder = LabelEncoder()
    encoder.fit(df["label"])
    class_names = list(encoder.classes_)

    baseline_accuracy = float(model.score(
        X_test, encoder.transform(y_test_labels),
    ))

    results = {"baseline_sklearn_accuracy": baseline_accuracy, "methods": {}}

    # 'loadable' só suporta dtype='int16_t' (restrição do próprio emlearn).
    # 'inline' testa-se nos dois dtypes, porque a primeira medição (int16
    # por omissão) revelou uma queda grande de accuracy — ver nota mais
    # abaixo e ml/README.md para a explicação.
    variants = [
        ("inline", "int16_t"),
        ("inline", "float"),
        ("loadable", "int16_t"),
    ]

    dtype_tag = {"int16_t": "int16", "float": "float"}

    original_cwd = Path.cwd()
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        # emlearn.common.CompiledClassifier (usado por measure_c_accuracy,
        # via wrapper.predict()) escreve binários de teste numa pasta "tmp"
        # RELATIVA ao diretório de trabalho atual, sem forma de a
        # configurar a partir do Wrapper — muda-se para o diretório
        # temporário desta medição para esses ficheiros não sujarem o
        # repositório (limpos automaticamente ao sair do "with" acima).
        os.chdir(workdir)
        try:
            for method, dtype in variants:
                variant_key = f"{method}_{dtype_tag[dtype]}"
                name = f"activity_classifier_rf_{variant_key}"
                _, header_path = generate_c_code(model, method, dtype, name, workdir)
                footprint = compile_and_measure(header_path, name, workdir)
                c_accuracy = measure_c_accuracy(
                    model, method, dtype, X_test, y_test_labels, class_names,
                )
                results["methods"][variant_key] = {
                    "method": method,
                    "dtype": dtype,
                    **footprint,
                    "c_accuracy": c_accuracy,
                    "generated_c_lines": header_path.read_text().count("\n"),
                }
        finally:
            os.chdir(original_cwd)

    results["free_flash_budget_bytes"] = FREE_FLASH_BYTES_BUDGET
    results["free_ram_budget_bytes"] = FREE_RAM_BYTES_BUDGET
    results["n_estimators"] = int(model.n_estimators)
    results["max_depth"] = model.max_depth
    results["n_features"] = int(model.n_features_in_)
    results["note"] = (
        "Medido compilando o C gerado pelo emlearn com arm-none-eabi-gcc "
        "para Cortex-M4F (-Os), o mesmo alvo do firmware. 'flash_bytes' e' "
        ".text+.rodata+.data de uma unica unidade de compilacao isolada (so "
        "o classificador, sem o resto do firmware) - nao inclui o overhead "
        "de runtime C (startup, libc) que o firmware completo ja paga por "
        "outras razoes. 'c_accuracy' corre o MESMO codigo C gerado "
        "(compilado nativo, nao simulado) para cada variante - ver "
        "ml/README.md para a descoberta real desta medicao: a quantizacao "
        "int16 (omissao do emlearn, usada em 'inline_int16' e em "
        "'loadable_int16') reduz a accuracy de 0.978 (sklearn) para bem "
        "menos, porque varias das nossas features tem valores tipicamente "
        "entre -1 e 1 (ex.: correlacao entre eixos) que colapsam para 0 "
        "quando truncados para inteiro. 'inline_float' evita isso (mantem "
        "os limiares em float) a troco de mais flash."
    )

    Path("reports").mkdir(exist_ok=True)
    with open(OUTPUT_REPORT, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"Accuracy original (sklearn, float64): {baseline_accuracy:.3f}")
    for variant_key, m in results["methods"].items():
        print(
            f"[{variant_key:16s}] flash={m['flash_bytes']:6d} bytes  "
            f"ram={m['ram_bytes']:4d} bytes  "
            f"accuracy (C)={m['c_accuracy']:.3f}  "
            f"({m['generated_c_lines']} linhas de C geradas)"
        )
    print(f"\nRelatorio escrito em {OUTPUT_REPORT}")


if __name__ == "__main__":
    main()
