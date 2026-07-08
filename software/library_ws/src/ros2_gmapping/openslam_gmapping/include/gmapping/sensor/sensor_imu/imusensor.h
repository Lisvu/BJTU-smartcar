#ifndef IMUSENSOR_H
#define IMUSENSOR_H

#include <string>
#include <gmapping/sensor/sensor_base/sensor.h>

namespace GMapping {

class IMUSensor: public Sensor {
public:
    IMUSensor(const std::string& name);
    virtual ~IMUSensor();
};

};

#endif 