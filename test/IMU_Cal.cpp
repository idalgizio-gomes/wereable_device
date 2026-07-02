#include "LSM6DS3.h"
#include "Wire.h"

LSM6DS3 imu(I2C_MODE, 0x6A);

// Offsets calculados na calibração
float gyro_offset_x = 0, gyro_offset_y = 0, gyro_offset_z = 0;
float accel_offset_x = 0, accel_offset_y = 0, accel_offset_z = 0;

const int NUM_SAMPLES = 500;

void calibrateIMU() {
  Serial.println("A calibrar IMU... Mantém o sensor completamente parado!");
  delay(3000); // Tempo para estabilizar

  double sum_gx = 0, sum_gy = 0, sum_gz = 0;
  double sum_ax = 0, sum_ay = 0, sum_az = 0;

  for (int i = 0; i < NUM_SAMPLES; i++) {
    sum_gx += imu.readFloatGyroX();
    sum_gy += imu.readFloatGyroY();
    sum_gz += imu.readFloatGyroZ();

    sum_ax += imu.readFloatAccelX();
    sum_ay += imu.readFloatAccelY();
    sum_az += imu.readFloatAccelZ();

    delay(5); // ~200 Hz
  }

  // Offsets do giroscópio (idealmente todos próximos de 0)
  gyro_offset_x = sum_gx / NUM_SAMPLES;
  gyro_offset_y = sum_gy / NUM_SAMPLES;
  gyro_offset_z = sum_gz / NUM_SAMPLES;

  // Offsets do acelerómetro
  // Se o sensor estiver flat, X e Y devem ser ~0, Z deve ser ~1g
  accel_offset_x = sum_ax / NUM_SAMPLES;
  accel_offset_y = sum_ay / NUM_SAMPLES;
  accel_offset_z = (sum_az / NUM_SAMPLES) - 1.0; // subtrai 1g da gravidade

  Serial.println("=== Calibração concluída ===");
  Serial.print("Gyro offsets (dps) -> X: "); Serial.print(gyro_offset_x, 4);
  Serial.print("  Y: "); Serial.print(gyro_offset_y, 4);
  Serial.print("  Z: "); Serial.println(gyro_offset_z, 4);

  Serial.print("Accel offsets (g)  -> X: "); Serial.print(accel_offset_x, 4);
  Serial.print("  Y: "); Serial.print(accel_offset_y, 4);
  Serial.print("  Z: "); Serial.println(accel_offset_z, 4);
}

void setup() {
  Serial.begin(115200);
  while (!Serial);

  if (imu.begin() != 0) {
    Serial.println("Erro ao iniciar o IMU!");
    while (1);
  }

  calibrateIMU();
}

void loop() {
  // Leituras compensadas com os offsets
  float gx = imu.readFloatGyroX()  - gyro_offset_x;
  float gy = imu.readFloatGyroY()  - gyro_offset_y;
  float gz = imu.readFloatGyroZ()  - gyro_offset_z;

  float ax = imu.readFloatAccelX() - accel_offset_x;
  float ay = imu.readFloatAccelY() - accel_offset_y;
  float az = imu.readFloatAccelZ() - accel_offset_z;

  Serial.print("Gyro (dps): ");
  Serial.print(gx, 3); Serial.print(", ");
  Serial.print(gy, 3); Serial.print(", ");
  Serial.println(gz, 3);

  Serial.print("Accel (g):  ");
  Serial.print(ax, 3); Serial.print(", ");
  Serial.print(ay, 3); Serial.print(", ");
  Serial.println(az, 3);

  delay(100);
}