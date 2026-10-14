{attribute 'qualified_only'}
VAR_GLOBAL
	SCADA_ADM100W_1 : DUT_SCADA_ADM100W;
	SCADA_DOSENSOR_1 : DUT_SCADA_DOSENSOR;
	SCADA_DIP1500_1 : DUT_SCADA_DIP1500;
END_VAR

// ------------------------------

TYPE DUT_EXC_ADM100W :
STRUCT
	Frequency				: REAL;		// Hz
	Voltage_LN				: REAL;		// V
	Current					: REAL;		// A
	Active_power			: REAL;		// W
	Apparent_power			: REAL;		// VA
	Reactive_power			: REAL;		// VAr
	Power_factor			: REAL;		// sans unite
	Voltage_THD				: REAL;		// %
	Current_THD				: REAL;		// %
	Max_load				: REAL;		// % de 60 A
	Internal_temperature	: REAL;		// degC
	Active_energy			: REAL;		// kWh
	Data_valid				: BOOL;		// FALSE = valeurs non exploitables
END_STRUCT
END_TYPE

// ------------------------------

TYPE DUT_IO_ADM100W :
STRUCT
END_STRUCT
END_TYPE

// ------------------------------

TYPE DUT_SCADA_ADM100W :
STRUCT
	OUTPUT_SCADA : ARRAY[0..87] OF WORD;
END_STRUCT
END_TYPE

// ------------------------------

TYPE DUT_EXC_DOSENSOR :
STRUCT
	OUTPUT_SIGNAL : REAL;
END_STRUCT
END_TYPE

// ------------------------------

TYPE DUT_IO_DOSENSOR :
STRUCT
//-----------INPUT	
	INPUT_SIGNAL : DWORD;
	INPUT_SENSOR_FAULT : BOOL;

//-----------OUTPUT
	OUTPUT_CALIBRATION_HIGH : BOOL;
	OUTPUT_CALIBRATION_LOW : BOOL;
	
END_STRUCT
END_TYPE

// ------------------------------

TYPE DUT_SCADA_DOSENSOR :
STRUCT
//---------INPUT
	INPUT_CALIBRATION_HIGH : BOOL;
	INPUT_CALIBRATION_LOW : BOOL;
	
//---------OUTPUT
	OUTPUT_SIGNAL : ARRAY[0..1] OF WORD;	
	OUTPUT_FAULT : BOOL;
	OUTPUT_CALIBRATION_DONE : BOOL;
END_STRUCT
END_TYPE

// ------------------------------

TYPE DUT_EXC_DIP1500 :
STRUCT
END_STRUCT
END_TYPE

// ------------------------------

TYPE DUT_IO_DIP1500 :
STRUCT
END_STRUCT
END_TYPE

// ------------------------------

TYPE DUT_SCADA_DIP1500 :
STRUCT
	//--------INPUT
	//BOOL
	INPUT_Run_CMD : BOOL;
	INPUT_Stop_CMD : BOOL;
	INPUT_Clockwise_Direction_CMD : BOOL;
	INPUT_Counterclockwise_Direction_CMD : BOOL;
	INPUT_Time_mode_CMD : BOOL;
	INPUT_Volume_mode_CMD : BOOL;
	INPUT_Save_Flash : BOOL;
	
	//Real
	INPUT_Speed : REAL;
	INPUT_Max_speed : REAL;
	INPUT_Run_time : REAL;
	INPUT_Calibre_Coef : REAL;
	
	//WORD
	INPUT_Foot_mode : WORD;
	INPUT_Level_mode : WORD;
	INPUT_StateOutMode : WORD;
	INPUT_Adress : WORD;
	INPUT_BAUD : WORD;
	
	
	
	
	//-------OUTPUT
	//BOOL
	OUTPUT_Pump_Running : BOOL;
	OUTPUT_Pump_Direction : BOOL;
	OUTPUT_Pump_Mode : BOOL;
	
	//Real
	OUTPUT_Pump_Speed : REAL;
	OUTPUT_Pump_Max_Speed : REAL;
	OUTPUT_Run_time : REAL;
	OUTPUT_Actual_time : REAL;
	OUTPUT_Total_Time : REAL;
	OUTPUT_Calib_Coef : REAL;
	
	//Word
	OUTPUT_Pump_firmware_version : WORD;
	OUTPUT_Pump_hardware : WORD;
	OUTPUT_Pump_temp : WORD;
	OUTPUT_pump_level_alarm : WORD;
	OUTPUT_WDig8out : WORD;
END_STRUCT
END_TYPE
