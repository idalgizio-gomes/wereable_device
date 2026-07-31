// ============================================================================
// GNSS.cpp — teste isolado do módulo GPS/GNSS (CAM-M8Q, u-blox) por I2C
// ----------------------------------------------------------------------------
// Ainda não confirmado em hardware (escrito sem placa disponível, 2026-07-31).
// Segue o mesmo padrão dos outros ficheiros desta pasta (SPO2.cpp, HR.cpp,
// IMU_Cal.cpp): um sketch Arduino autónomo (setup()/loop() próprios), NÃO
// compilado por omissão pelo ambiente principal — para o correr, ou troca-se
// temporariamente por src/main.cpp, ou acrescenta-se um ambiente PlatformIO
// dedicado (ver [env:test_lora_isolated] em platformio.ini para o padrão a
// copiar, com build_src_filter = -<*> +<GNSS.cpp> apontado para test/).
//
// O que já se sabe (PROJECT_STATUS.md, "Descobertas do esquemático real"):
// existe mesmo um módulo GPS real (CAM-M8Q, u-blox) na placa, ligado por I2C
// — não é uma suposição, foi confirmado no esquemático custom da placa. O
// que NÃO se sabe ainda (sem confirmação física): em qual dos dois
// barramentos I2C do XIAO (Wire interno vs Wire1/externo em D4-D5, este
// último partilhado com o PPG — ver PPG_USE_EXTERNAL_WIRE_ONLY em Ppg.cpp)
// o módulo está ligado, nem se partilha o mesmo barramento do PPG/IMU. Por
// isso este teste tenta os dois, tal como o Ppg.cpp fazia originalmente
// antes de essa dúvida ser resolvida em hardware para o PPG.
//
// Endereço I2C: 0x42 é o endereço por omissão de módulos u-blox (incluindo
// CAM-M8Q) — não encontrada nenhuma nota no esquemático a sugerir um
// endereço diferente, por isso usa-se o valor por omissão da biblioteca
// (SFE_UBLOX_GNSS::begin(), ver SparkFun_u-blox_GNSS_Arduino_Library.h).
//
// Baseado no exemplo oficial da biblioteca (Example3_GetPosition.ino,
// SparkFun u-blox GNSS Arduino Library, já instalada em lib_deps do
// projeto), adaptado ao estilo dos outros testes desta pasta: mensagens em
// português, tentativa nos dois barramentos, e um resumo do que cada
// resultado significa (não só o valor em bruto).
// ============================================================================

#include <Wire.h>
#include <SparkFun_u-blox_GNSS_Arduino_Library.h>

SFE_UBLOX_GNSS gnss;

// Intervalo mínimo entre queries (ms) — o módulo só responde quando tem uma
// posição nova; perguntar mais depressa do que isto só gera tráfego I2C
// inútil (mesmo espírito do exemplo oficial).
constexpr uint32_t kQueryIntervalMs = 1000;

TwoWire *g_gnssBus = nullptr;
const char *g_gnssBusName = "N/A";

// Tenta inicializar o GNSS em Wire (interno) e, se falhar, em Wire1
// (externo, D4/D5 — o mesmo barramento onde o PPG está confirmado). Devolve
// true e regista o barramento encontrado em g_gnssBus/g_gnssBusName assim
// que o módulo responder num dos dois.
bool beginGnss() {
  struct Candidate {
    TwoWire *bus;
    const char *name;
  };
  const Candidate candidates[] = {
      {&Wire, "Wire (interno)"},
      {&Wire1, "Wire1 (externo, D4/D5)"},
  };

  for (const auto &candidate : candidates) {
    Serial.print(F("[GNSS] a tentar barramento "));
    Serial.print(candidate.name);
    Serial.println(F("..."));

    candidate.bus->begin();
    if (gnss.begin(*candidate.bus)) {
      g_gnssBus = candidate.bus;
      g_gnssBusName = candidate.name;
      Serial.print(F("[GNSS] modulo encontrado em "));
      Serial.println(candidate.name);
      return true;
    }
    Serial.print(F("[GNSS] nao respondeu em "));
    Serial.println(candidate.name);
  }

  return false;
}

void setup() {
  Serial.begin(115200);
  uint32_t serialWaitStart = millis();
  while (!Serial && (millis() - serialWaitStart) < 5000) {
    // Espera até 5s pela porta série (mesmo padrão usado nos outros testes
    // desta pasta) — nunca bloqueia para sempre se não houver monitor
    // ligado, ao contrário de um while(!Serial) simples.
  }

  Serial.println(F("\n=== Teste isolado: GNSS (CAM-M8Q, u-blox) ==="));

  if (!beginGnss()) {
    Serial.println(F("[GNSS] ERRO: modulo nao detetado em nenhum dos dois barramentos I2C."));
    Serial.println(F("[GNSS] Verificar: alimentacao do modulo, ligacoes SDA/SCL, e se o"));
    Serial.println(F("[GNSS] endereco I2C e mesmo 0x42 (confirmar no esquemático real)."));
    while (true) {
      delay(1000);
    }
  }

  // Reduz o tráfego I2C: só UBX binário, sem o ruído das sentenças NMEA
  // (mesma otimização do exemplo oficial — sem isto o módulo também
  // funciona, mas com mais tráfego I2C desnecessário).
  gnss.setI2COutput(COM_TYPE_UBX);

  Serial.println(F("[GNSS] configurado. A aguardar fix de satelites..."));
  Serial.println(F("[GNSS] (pode demorar minutos num primeiro arranque frio, ou se"));
  Serial.println(F("[GNSS] testado dentro de um edificio sem vista para o ceu)"));
}

void loop() {
  static uint32_t lastQueryMs = 0;
  const uint32_t now = millis();
  if (now - lastQueryMs < kQueryIntervalMs) {
    return;
  }
  lastQueryMs = now;

  const bool hasFix = gnss.getGnssFixOk();
  const byte siv = gnss.getSIV();  // Satellites In View usados na solução atual.

  Serial.print(F("[GNSS] fix="));
  Serial.print(hasFix ? "SIM" : "NAO");
  Serial.print(F(" siv="));
  Serial.print(siv);

  if (!hasFix) {
    // Sem fix, lat/long/altitude ainda não são fiáveis — não vale a pena
    // imprimi-los como se fossem uma posição real (podem ser lixo/zero).
    Serial.println(F(" (sem posicao valida ainda)"));
    return;
  }

  const long latitude = gnss.getLatitude();    // graus * 10^7
  const long longitude = gnss.getLongitude();  // graus * 10^7
  const long altitudeMm = gnss.getAltitude();  // mm acima do elipsoide

  Serial.print(F(" lat="));
  Serial.print(latitude);
  Serial.print(F(" (graus*1e-7) long="));
  Serial.print(longitude);
  Serial.print(F(" (graus*1e-7) alt="));
  Serial.print(altitudeMm);
  Serial.println(F("mm"));
}
