/*****************************************************************//**
 * \file   HeraAPI_Status.h
 * \brief  
 * 
 * \author Nireos
 * \date   May 2024
 *********************************************************************/

#pragma once

#include "HeraAPI_Platforms.h"


HERA_API_EXTERNC_BEG

	/**
	 * \typedef HERA_API_STR
	 * \brief Internal API alias for a pointer to a null terminated string.
	 */
	typedef const char* HERA_API_STR;

	/**
	 * \typedef HERA_API_STATUS
	 * \brief Hera API return status code.
	 */
	typedef enum HERA_API_STATUS
	{
		HERA_API_OK = 0,					/**< Operation success. */	
		HERA_API_FAIL = 0x80000008,			/**< Operation failure. */
		HERA_API_CORRUPTED,					/**< Operation failed due to a corrupted environment. */
		HERA_API_NOT_LICENSED,				/**< License not activated, invalid or impossible to do an online check. */
		HERA_API_CANNOT_APPLY_LICENSE,		/**< License activation failed. */
		HERA_API_INVALID_ARG,				/**< Generic invalid argument. */
		HERA_API_INVALID_ARG_OUTOFBOUNDS,	/**< Out of bounds argument.  */
		HERA_API_INVALID_HANDLE,			/**< Invalid input handle. */
		HERA_API_DEVICE_ALREADY_CREATED,	/**< Creating an already existing device. */
		HERA_API_CANNOT_SET_PARAMETER,		/**< Impossible to set the parameter. */
		HERA_API_CANNOT_GET_PARAMETER,		/**< Impossible to get the parameter. */
		HERA_API_CANNOT_EXECUTE_FUNCTION,	/**< Impossible to execute the function. */
	} HERA_API_STATUS;

	/**
	 * \typedef HYPERSPECTRAL_DATA_STATUS
	 * \brief Hera hyperspectral acuqisition outcome.
	 */
	typedef enum HYPERSPECTRAL_DATA_STATUS
	{
		OK,					/**< Valid hyperspectral data acuisition. */
		ABORTED,			/**< Hyperspectral data acquisition was aborted. */
		DEVICE_TIMEOUT,		/**< Device did not responde until timeout. */
		DEVICE_LOST,		/**< Device connection was lost. */
		CAMERA_INIT_ERROR,	/**< Device could not be initialized. */
		CAPTURE_ERROR,		/**< Frame acquisition failed. */
		UNKNOWN,			/**< Acquisition failed for unknown reason. */
	}HYPERSPECTRAL_DATA_STATUS;

HERA_API_EXTERNC_END