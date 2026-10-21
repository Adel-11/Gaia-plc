(* ===== FC_EXC_UPI_LOCAL_FUXA_ADM100W ===== *)
FUNCTION FC_EXC_UPI_LOCAL_FUXA_ADM100W : BOOL
VAR_IN_OUT
	EXC_UPI_LOCAL_FUXA : DUT_EXC_UPI_LOCAL_FUXA;   // Exchanges of the UPI_LOCAL_FUXA instance
	EXC_ADM100W : DUT_EXC_ADM100W;   // Exchanges of the ADM100W instance
END_VAR

//==============================================================================
// FC_EXC_UPI_LOCAL_FUXA_ADM100W
// Exchanges between one UPI_LOCAL_FUXA instance and one ADM100W instance
// Called in GFC_EXC, once per pair of instances, with their GVL_EXC variables
// Simple copies only, no conversion
//==============================================================================

//--- UPI_LOCAL_FUXA -> ADM100W
EXC_ADM100W.UPI_INPUT_GLOBAL_LOCAL_FUXA := EXC_UPI_LOCAL_FUXA.UPI_OUTPUT_LOCAL_ACTIVE;

FC_EXC_UPI_LOCAL_FUXA_ADM100W := TRUE;

(* ===== FC_EXC_UPI_LOCAL_HMI_ADM100W ===== *)
FUNCTION FC_EXC_UPI_LOCAL_HMI_ADM100W : BOOL
VAR_IN_OUT
	EXC_UPI_LOCAL_HMI : DUT_EXC_UPI_LOCAL_HMI;   // Exchanges of the UPI_LOCAL_HMI instance
	EXC_ADM100W : DUT_EXC_ADM100W;   // Exchanges of the ADM100W instance
END_VAR

//==============================================================================
// FC_EXC_UPI_LOCAL_HMI_ADM100W
// Exchanges between one UPI_LOCAL_HMI instance and one ADM100W instance
// Called in GFC_EXC, once per pair of instances, with their GVL_EXC variables
// Simple copies only, no conversion
//==============================================================================

//--- UPI_LOCAL_HMI -> ADM100W
EXC_ADM100W.UPI_INPUT_GLOBAL_LOCAL_HMI := EXC_UPI_LOCAL_HMI.UPI_OUTPUT_LOCAL_ACTIVE;

FC_EXC_UPI_LOCAL_HMI_ADM100W := TRUE;

(* ===== FC_EXC_UPI_LOCAL_WEBVISU_ADM100W ===== *)
FUNCTION FC_EXC_UPI_LOCAL_WEBVISU_ADM100W : BOOL
VAR_IN_OUT
	EXC_UPI_LOCAL_WEBVISU : DUT_EXC_UPI_LOCAL_WEBVISU;   // Exchanges of the UPI_LOCAL_WEBVISU instance
	EXC_ADM100W : DUT_EXC_ADM100W;   // Exchanges of the ADM100W instance
END_VAR

//==============================================================================
// FC_EXC_UPI_LOCAL_WEBVISU_ADM100W
// Exchanges between one UPI_LOCAL_WEBVISU instance and one ADM100W instance
// Called in GFC_EXC, once per pair of instances, with their GVL_EXC variables
// Simple copies only, no conversion
//==============================================================================

//--- UPI_LOCAL_WEBVISU -> ADM100W
EXC_ADM100W.UPI_INPUT_GLOBAL_LOCAL_WEBVISU := EXC_UPI_LOCAL_WEBVISU.UPI_OUTPUT_LOCAL_ACTIVE;

FC_EXC_UPI_LOCAL_WEBVISU_ADM100W := TRUE;

(* ===== FC_EXC_UPI_LOCAL_FUXA_DOSENSOR ===== *)
FUNCTION FC_EXC_UPI_LOCAL_FUXA_DOSENSOR : BOOL
VAR_IN_OUT
	EXC_UPI_LOCAL_FUXA : DUT_EXC_UPI_LOCAL_FUXA;   // Exchanges of the UPI_LOCAL_FUXA instance
	EXC_DOSENSOR : DUT_EXC_DOSENSOR;   // Exchanges of the DOSENSOR instance
END_VAR

//==============================================================================
// FC_EXC_UPI_LOCAL_FUXA_DOSENSOR
// Exchanges between one UPI_LOCAL_FUXA instance and one DOSENSOR instance
// Called in GFC_EXC, once per pair of instances, with their GVL_EXC variables
// Simple copies only, no conversion
//==============================================================================

//--- UPI_LOCAL_FUXA -> DOSENSOR
EXC_DOSENSOR.UPI_INPUT_GLOBAL_LOCAL_FUXA := EXC_UPI_LOCAL_FUXA.UPI_OUTPUT_LOCAL_ACTIVE;

FC_EXC_UPI_LOCAL_FUXA_DOSENSOR := TRUE;

(* ===== FC_EXC_UPI_LOCAL_HMI_DOSENSOR ===== *)
FUNCTION FC_EXC_UPI_LOCAL_HMI_DOSENSOR : BOOL
VAR_IN_OUT
	EXC_UPI_LOCAL_HMI : DUT_EXC_UPI_LOCAL_HMI;   // Exchanges of the UPI_LOCAL_HMI instance
	EXC_DOSENSOR : DUT_EXC_DOSENSOR;   // Exchanges of the DOSENSOR instance
END_VAR

//==============================================================================
// FC_EXC_UPI_LOCAL_HMI_DOSENSOR
// Exchanges between one UPI_LOCAL_HMI instance and one DOSENSOR instance
// Called in GFC_EXC, once per pair of instances, with their GVL_EXC variables
// Simple copies only, no conversion
//==============================================================================

//--- UPI_LOCAL_HMI -> DOSENSOR
EXC_DOSENSOR.UPI_INPUT_GLOBAL_LOCAL_HMI := EXC_UPI_LOCAL_HMI.UPI_OUTPUT_LOCAL_ACTIVE;

FC_EXC_UPI_LOCAL_HMI_DOSENSOR := TRUE;

(* ===== FC_EXC_UPI_LOCAL_WEBVISU_DOSENSOR ===== *)
FUNCTION FC_EXC_UPI_LOCAL_WEBVISU_DOSENSOR : BOOL
VAR_IN_OUT
	EXC_UPI_LOCAL_WEBVISU : DUT_EXC_UPI_LOCAL_WEBVISU;   // Exchanges of the UPI_LOCAL_WEBVISU instance
	EXC_DOSENSOR : DUT_EXC_DOSENSOR;   // Exchanges of the DOSENSOR instance
END_VAR

//==============================================================================
// FC_EXC_UPI_LOCAL_WEBVISU_DOSENSOR
// Exchanges between one UPI_LOCAL_WEBVISU instance and one DOSENSOR instance
// Called in GFC_EXC, once per pair of instances, with their GVL_EXC variables
// Simple copies only, no conversion
//==============================================================================

//--- UPI_LOCAL_WEBVISU -> DOSENSOR
EXC_DOSENSOR.UPI_INPUT_GLOBAL_LOCAL_WEBVISU := EXC_UPI_LOCAL_WEBVISU.UPI_OUTPUT_LOCAL_ACTIVE;

FC_EXC_UPI_LOCAL_WEBVISU_DOSENSOR := TRUE;

(* ===== FC_EXC_UPI_LOCAL_FUXA_DIP1500 ===== *)
FUNCTION FC_EXC_UPI_LOCAL_FUXA_DIP1500 : BOOL
VAR_IN_OUT
	EXC_UPI_LOCAL_FUXA : DUT_EXC_UPI_LOCAL_FUXA;   // Exchanges of the UPI_LOCAL_FUXA instance
	EXC_DIP1500 : DUT_EXC_DIP1500;   // Exchanges of the DIP1500 instance
END_VAR

//==============================================================================
// FC_EXC_UPI_LOCAL_FUXA_DIP1500
// Exchanges between one UPI_LOCAL_FUXA instance and one DIP1500 instance
// Called in GFC_EXC, once per pair of instances, with their GVL_EXC variables
// Simple copies only, no conversion
//==============================================================================

//--- UPI_LOCAL_FUXA -> DIP1500
EXC_DIP1500.UPI_INPUT_GLOBAL_LOCAL_FUXA := EXC_UPI_LOCAL_FUXA.UPI_OUTPUT_LOCAL_ACTIVE;

FC_EXC_UPI_LOCAL_FUXA_DIP1500 := TRUE;

(* ===== FC_EXC_UPI_LOCAL_HMI_DIP1500 ===== *)
FUNCTION FC_EXC_UPI_LOCAL_HMI_DIP1500 : BOOL
VAR_IN_OUT
	EXC_UPI_LOCAL_HMI : DUT_EXC_UPI_LOCAL_HMI;   // Exchanges of the UPI_LOCAL_HMI instance
	EXC_DIP1500 : DUT_EXC_DIP1500;   // Exchanges of the DIP1500 instance
END_VAR

//==============================================================================
// FC_EXC_UPI_LOCAL_HMI_DIP1500
// Exchanges between one UPI_LOCAL_HMI instance and one DIP1500 instance
// Called in GFC_EXC, once per pair of instances, with their GVL_EXC variables
// Simple copies only, no conversion
//==============================================================================

//--- UPI_LOCAL_HMI -> DIP1500
EXC_DIP1500.UPI_INPUT_GLOBAL_LOCAL_HMI := EXC_UPI_LOCAL_HMI.UPI_OUTPUT_LOCAL_ACTIVE;

FC_EXC_UPI_LOCAL_HMI_DIP1500 := TRUE;

(* ===== FC_EXC_UPI_LOCAL_WEBVISU_DIP1500 ===== *)
FUNCTION FC_EXC_UPI_LOCAL_WEBVISU_DIP1500 : BOOL
VAR_IN_OUT
	EXC_UPI_LOCAL_WEBVISU : DUT_EXC_UPI_LOCAL_WEBVISU;   // Exchanges of the UPI_LOCAL_WEBVISU instance
	EXC_DIP1500 : DUT_EXC_DIP1500;   // Exchanges of the DIP1500 instance
END_VAR

//==============================================================================
// FC_EXC_UPI_LOCAL_WEBVISU_DIP1500
// Exchanges between one UPI_LOCAL_WEBVISU instance and one DIP1500 instance
// Called in GFC_EXC, once per pair of instances, with their GVL_EXC variables
// Simple copies only, no conversion
//==============================================================================

//--- UPI_LOCAL_WEBVISU -> DIP1500
EXC_DIP1500.UPI_INPUT_GLOBAL_LOCAL_WEBVISU := EXC_UPI_LOCAL_WEBVISU.UPI_OUTPUT_LOCAL_ACTIVE;

FC_EXC_UPI_LOCAL_WEBVISU_DIP1500 := TRUE;

(* ===== GFC_EXC ===== *)
FUNCTION GFC_EXC : BOOL

//==============================================================================
// GFC_EXC
// Exchanges between instances : one FC_EXC_<A>_<B> call per pair of instances
// Called after GFB_UPI and before GFB_INST
//==============================================================================

//------------------------------------------------------------------------------
// ADM100W_1
//------------------------------------------------------------------------------
	//--- UPI_LOCAL_FUXA <-> ADM100W_1
	FC_EXC_UPI_LOCAL_FUXA_ADM100W(
		EXC_UPI_LOCAL_FUXA := GVL_EXC.EXC_UPI_LOCAL_FUXA,
		EXC_ADM100W        := GVL_EXC.EXC_ADM100W_1);

	//--- UPI_LOCAL_HMI <-> ADM100W_1
	FC_EXC_UPI_LOCAL_HMI_ADM100W(
		EXC_UPI_LOCAL_HMI := GVL_EXC.EXC_UPI_LOCAL_HMI,
		EXC_ADM100W       := GVL_EXC.EXC_ADM100W_1);

	//--- UPI_LOCAL_WEBVISU <-> ADM100W_1
	FC_EXC_UPI_LOCAL_WEBVISU_ADM100W(
		EXC_UPI_LOCAL_WEBVISU := GVL_EXC.EXC_UPI_LOCAL_WEBVISU,
		EXC_ADM100W           := GVL_EXC.EXC_ADM100W_1);


//------------------------------------------------------------------------------
// DOSENSOR_1
//------------------------------------------------------------------------------
	//--- UPI_LOCAL_FUXA <-> DOSENSOR_1
	FC_EXC_UPI_LOCAL_FUXA_DOSENSOR(
		EXC_UPI_LOCAL_FUXA := GVL_EXC.EXC_UPI_LOCAL_FUXA,
		EXC_DOSENSOR       := GVL_EXC.EXC_DOSENSOR_1);

	//--- UPI_LOCAL_HMI <-> DOSENSOR_1
	FC_EXC_UPI_LOCAL_HMI_DOSENSOR(
		EXC_UPI_LOCAL_HMI := GVL_EXC.EXC_UPI_LOCAL_HMI,
		EXC_DOSENSOR      := GVL_EXC.EXC_DOSENSOR_1);

	//--- UPI_LOCAL_WEBVISU <-> DOSENSOR_1
	FC_EXC_UPI_LOCAL_WEBVISU_DOSENSOR(
		EXC_UPI_LOCAL_WEBVISU := GVL_EXC.EXC_UPI_LOCAL_WEBVISU,
		EXC_DOSENSOR          := GVL_EXC.EXC_DOSENSOR_1);


//------------------------------------------------------------------------------
// DIP1500_1
//------------------------------------------------------------------------------
	//--- UPI_LOCAL_FUXA <-> DIP1500_1
	FC_EXC_UPI_LOCAL_FUXA_DIP1500(
		EXC_UPI_LOCAL_FUXA := GVL_EXC.EXC_UPI_LOCAL_FUXA,
		EXC_DIP1500        := GVL_EXC.EXC_DIP1500_1);

	//--- UPI_LOCAL_HMI <-> DIP1500_1
	FC_EXC_UPI_LOCAL_HMI_DIP1500(
		EXC_UPI_LOCAL_HMI := GVL_EXC.EXC_UPI_LOCAL_HMI,
		EXC_DIP1500       := GVL_EXC.EXC_DIP1500_1);

	//--- UPI_LOCAL_WEBVISU <-> DIP1500_1
	FC_EXC_UPI_LOCAL_WEBVISU_DIP1500(
		EXC_UPI_LOCAL_WEBVISU := GVL_EXC.EXC_UPI_LOCAL_WEBVISU,
		EXC_DIP1500           := GVL_EXC.EXC_DIP1500_1);

GFC_EXC := TRUE;

(* ===== PLC_PRG (body) ===== *)
//==============================================================================
// PLC_PRG
// Main cycle : 1. read all inputs, 2. functional units, 3. equipment, 4. write all outputs
//==============================================================================

//------------------------------------------------------------------------------
// 1. INPUTS : interfaces -> GVL
//------------------------------------------------------------------------------
GFC_COM_IN();
GFC_IO_IN();

//------------------------------------------------------------------------------
// 2. FUNCTIONAL UNITS (UPI) and exchanges between instances
//------------------------------------------------------------------------------
GFB_UPI_1();
GFC_EXC();      // exchanges between instances (FC_EXC_...)

//------------------------------------------------------------------------------
// 3. EQUIPMENT
//------------------------------------------------------------------------------
GFB_INST_1();

//------------------------------------------------------------------------------
// 4. OUTPUTS : GVL -> interfaces
//------------------------------------------------------------------------------
GFC_COM_OUT();
GFC_IO_OUT();
