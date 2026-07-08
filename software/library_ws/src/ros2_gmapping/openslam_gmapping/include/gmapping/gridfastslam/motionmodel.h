#ifndef MOTIONMODEL_H
#define MOTIONMODEL_H

#include <gmapping/utils/point.h>
#include <gmapping/utils/stat.h>
#include <gmapping/utils/macro_params.h>

namespace  GMapping { 

struct MotionModel{
	OrientedPoint drawFromMotion(const OrientedPoint& p, double linearMove, double angularMove) const;
	OrientedPoint drawFromMotion(const OrientedPoint& p, const OrientedPoint& pnew, const OrientedPoint& pold) const;
	Covariance3 gaussianApproximation(const OrientedPoint& pnew, const OrientedPoint& pold) const;
	double srr, str, srt, stt;
	bool useIMU;  // 是否使用IMU数据
	double imuAngularWeight;  // IMU角度信息权重
	
	OrientedPoint drawFromMotionWithIMU(const OrientedPoint& p, const OrientedPoint& pnew, 
										const OrientedPoint& pold, double imuOrientation) const;
};

};

#endif
