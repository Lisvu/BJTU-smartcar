#ifndef IMUREADING_H
#define IMUREADING_H

#include <gmapping/sensor/sensor_base/sensorreading.h>
#include <gmapping/utils/point.h>
#include "imusensor.h"

namespace GMapping {

class IMUReading: public SensorReading {
public:
    IMUReading(const IMUSensor* imu, double time=0);
    
    inline double getAngularVelocity() const { return m_angularVelocity; }
    inline double getLinearAccelerationX() const { return m_linearAccelX; }
    inline double getLinearAccelerationY() const { return m_linearAccelY; }
    inline double getLinearAccelerationZ() const { return m_linearAccelZ; }
    inline double getOrientation() const { return m_orientation; }
    
    inline void setAngularVelocity(double av) { m_angularVelocity = av; }
    inline void setLinearAcceleration(double x, double y, double z) { 
        m_linearAccelX = x; m_linearAccelY = y; m_linearAccelZ = z; 
    }
    inline void setOrientation(double o) { m_orientation = o; }
    
protected:
    double m_angularVelocity;
    double m_linearAccelX;
    double m_linearAccelY;
    double m_linearAccelZ;
    double m_orientation;
};

};

#endif 