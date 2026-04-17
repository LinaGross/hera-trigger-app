/*****************************************************************//**
 * \file   HeraAPI.h
 *
 * \author Nireos
 * \date   July 2025
 *********************************************************************/

 /**
  * \mainpage Home Page
  *
  *
  * Welcome to Hera API documentation. Nireos provides an SDK to interact with any HERA devices, straight from the code, in a simple and intuitive way. Hera API is a low level, 
  * easy-to-use and flexible API, written in C to ensure a stable ABI and it can be used to develop C/C++ applications.
  * 
  * API provides functions for:
  * -	Device enumeration
  * -	Controlling device parameters
  * -	Live acquisition
  * -	Hyperspectral data acquisition
  * -	Events notification
  *
  * \subsection principles Basic Principles
  *
  * \subsubsection memory Resource and memory management
  * Resources are identified by handles. The library allocates a resource internally and returns its handle. From now on, any operation on the resource can be done by using the related handle. At the end a resource must be deallocated using
  * the respective HeraAPI_ReleaseXXX() function. Note that by using the HeraAPI_Cleanup() function, every handle previously returned by the library will be deallocated and invalidated.
  *
  * \subsubsection errors Error handling
  * Every API function returns a status code informing about the just performed operation. An error occurred if the returning value is different from HERA_API_OK. For any possible error value, see the HERA_API_STATUS enumeration inside the HeraAPI_Status.h file.
  * In case of errors, additional details can be retrieved via the HeraAPI_GetLastErrorMessage() function which returns a null-terminated string containing human readable information.
  *
  *
  * \subsubsection events Notifications and Events
  * Notifications are implemented as Callbacks and there are many functions in the HeraAPI_RegisterXXXCallback() form allowing registration to one or more specific events. You can also discard a previously made registration by using the respective
  * HeraAPI_UnregisterXXXCallback() function.
  *
  *
  *
  * \subsection compatibility Compatibility
  * Currently the only supported platform is Microsoft Windows x64 (version 7 or higher).
  */

#pragma once

#include "HeraAPI_Platforms.h"
#include "HeraAPI_Version.h"
#include "HeraAPI_Status.h"

HERA_API_EXTERNC_BEG

    /**
     * \cond EXCLUDED
     */
    #define HERA_API_DECLARE_HANDLE(name) struct name##_; typedef struct name##_ *name
    #ifndef HeraDeviceInfoEx
    #define HeraDeviceInfoEx
    #endif
    /**
     * \endcond
     */    

    #define HERA_API_CHAR_BUFFER_SIZE 128
    #define HERA_API_PATH_SIZE 1024
    #define HERA_API_SMALL_VEC_SIZE 16
    #define HERA_API_BIG_VEC_SIZE 2048

     /**
      * \enum HeraScanMode
      * \brief Defines the available scan modes for hyperspectral acquisitions.
      *
      * This enumeration specifies the type of scan that can be performed.
      * The longer the scan, the higher the spectral resolution.
      */
    typedef enum HeraScanMode
    {
        /**
         * Short scan mode: fast acquisition, lower spectral resolution.
         */
        Short,

        /**
         * Medium scan mode: balanced acquisition speed and resolution.
         */
        Medium,

        /**
         * Long scan mode: slow acquisition, higher spectral resolution.
         */
        Long,

        /**
         * ExtraLong scan mode: slower acquisition, highest spectral resolution.
         */
        ExtraLong

    } HeraScanMode;

    /**
    * \enum HeraPixelFormat
    * \brief Defines pixel formats available for image acquisition.
    *
    * Each value indicates the bit depth per pixel.
    */
    typedef enum HeraPixelFormat
    {
        /**
         * 8-bit monochrome.
         */
        Mono8,

        /**
         * 10-bit monochrome.
         */
        Mono10,

        /**
         * 12-bit monochrome.
         */
        Mono12,

        /**
         * 14-bit monochrome.
         */
        Mono14,

        /**
         * 16-bit monochrome.
         */
        Mono16
    } HeraPixelFormat;

    /**
     * \enum HeraTriggerMode
     * \brief Defines trigger modes for controlling acquisitions.
     *
     * These modes determine how hyperspectral acquisitions are initiated.
     */
    typedef enum HeraTriggerMode
    {
        /**
        * Internal trigger mode: acquisitions start automatically.
        */
        Internal,

        /**
        * Acquisition starts on external high signal.
        */
        DeferredStartExtLineHi,

        /**
        * Step-scan triggered by external low-to-high signal transition.
        */
        StepScanExtLoHi
    } HeraTriggerMode;

    /**
     * \enum HeraTemperatureStatus
     * \brief Represents the device temperature status.
     *
     * Describes whether the device temperature is within safe limits.
     */
    typedef enum HeraTemperatureStatus
    {
        /**
         * Temperature information not available.
         */
        NotAvailable = -1,

        /**
         * Temperature is within safe limits.
         */
        Ok = 0,

        /**
         * Temperature is elevated; caution advised.
         */
        Warning,

        /**
         * Temperature is critical; immediate action required.
         */
        Critical
    } HeraTemperatureStatus;

    /**
     * \enum HeraDataType
     * \brief Defines data types for hyperspectral data.
     *
     * Determines the numeric precision used in hyperspectral cubes.
     */
    typedef enum HeraDataType
    {
        /**
         * Single-precision floating point (32-bit).
         */
        SinglePrecision,

        /**
         * Double-precision floating point (64-bit).
         */
        DoublePrecision
    } HeraDataType;

    /**
     * \enum HeraBinningFactor
     * \brief Defines binning strategies for hyperspectral processing.
     *
     * Binning reduces data size and computation time by combining adjacent bands.
     * Enhanced binning modes provide higher quality.
     */
    typedef enum HeraBinningFactor
    {
        /**
         * No binning.
         */
        None = 0x0000,

        /**
         * 2x binning.
         */
        Bin2x,

        /**
         * 4x binning.
         */
        Bin4x,

        /**
         * 8x binning.
         */
        Bin8x,

        /**
         * Enhanced 2x binning for improved quality.
         */
        Bin2xEnhanced = 0x1000,

        /**
         * Enhanced 4x binning for improved quality.
         */
        Bin4xEnhanced
    } HeraBinningFactor;

    /**
     * \enum HeraResourceUsage
     * \brief Specifies resource usage levels for the API.
     *
     * Higher levels may improve performance but increase CPU/memory use.
     */
    typedef enum HeraResourceUsage
    {
        /**
         * Low resource usage.
         */
        LowUsage,

        /**
         * Medium resource usage.
         */
        MediumUsage,

        /**
         * High resource usage.
         */
        HighUsage,

        /**
         * Very high resource usage.
         */
        VeryHighUsage,

        /**
         * Maximum resource usage.
         */
        MaxUsage
    } HeraResourceUsage;

    /**
    * \enum ProgressStatus
    * \brief Represents the current status of a long-running operation.
    *
    * Used in the \c ProgressCallback to indicate whether the operation is
    * running normally, has completed with an error, or was aborted by the user.
    */
    typedef enum ProgressStatus
    {
        ProgressOk = 0,    /**< Operation is running normally or completed successfully */
        ProgressError,     /**< An error occurred during the operation */
        ProgressAborted    /**< Operation was cancelled by the user */
    } ProgressStatus;

    /**
     * \typedef HeraScanModeVec
     * \brief Array of HeraScanMode elements with fixed size.
     *
     * Useful for retrieving or storing a list of supported scan modes.
     * 
     */
    typedef HeraScanMode HeraScanModeVec[HERA_API_SMALL_VEC_SIZE];

    /**
     * @brief Structure containing information about a Hera device.
     *
     * This structure holds various details about a Hera device, such as
     * its unique identifier, product name, serial number, and vendor information.
     */
    typedef struct HeraDeviceInfo
    {
        /**
         * @brief Unique identifier of the device.
         */
        char Id[HERA_API_CHAR_BUFFER_SIZE];

        /**
         * @brief Name of the product.
         */
        char ProductName[HERA_API_CHAR_BUFFER_SIZE];

        /**
         * @brief Serial number of the device.
         */
        char SerialNumber[HERA_API_CHAR_BUFFER_SIZE];

        /**
         * @brief Vendor name of the device.
         */
        char Vendor[HERA_API_CHAR_BUFFER_SIZE];

        /**
         * \cond EXCLUDED
         */
        HeraDeviceInfoEx;
        /**
         * \endcond
         */

    } HeraDeviceInfo;

    HERA_API_DECLARE_HANDLE(HeraDeviceHandle);
    HERA_API_DECLARE_HANDLE(LiveCaptureHandle);
    HERA_API_DECLARE_HANDLE(HyperspectralDataHandle);
    HERA_API_DECLARE_HANDLE(HyperCubeHandle);

    /**
     * \typedef VoidCallback
     * \brief Function pointer type for a callback with no parameters and no return value.
     */
    typedef void(HERA_API_CC* VoidCallback)(void);

    /**
     * \typedef StrCallback
     * \brief Function pointer types for callbacks with specific parameters.
     *
     * These types define the signatures for callback functions that can be registered to handle various events in the Hera API.
	 */
    typedef void(HERA_API_CC* StrCallback)(HERA_API_STR);

    /**
     * \typedef IntCallback
     * \brief Function pointer type for a callback taking an integer parameter.
     *
     * \param[in] int Integer parameter.
	 */
    typedef void(HERA_API_CC* IntCallback)(int);

    /**
     * \typedef FloatCallback
     * \brief Function pointer type for a callback taking a float parameter.
     *
     * \param[in] float Float parameter.
	 */
    typedef void(HERA_API_CC* FloatCallback)(float);

    /**
    * \typedef ProgressCallback
    * \brief Callback function to report progress and status of a long-running operation.
    *
    * \param status Current status of the operation, see \c ProgressStatus.
    * \param progress Current progress as a float (0.0 to 1.0)
    * \param userData Optional user-defined pointer passed to the callback for context
    * \return Return a negative value to cancel the operation; return >=0 to continue.
    */
    typedef int(HERA_API_CC* ProgressCallback)(ProgressStatus status, float progress, void* userData);

    /**
     * \typedef LiveCaptureCallback
     * \brief Function pointer type for a callback that handles live capture events.
     *
     * This callback is invoked when a live capture event occurs, providing a handle to the captured data.
     *
     * \param[in] LiveCaptureHandle Handle to the live capture data.
	 */
    typedef void(HERA_API_CC* LiveCaptureCallback)(LiveCaptureHandle);

    /**
     * \typedef HyperspectralDataCallback
     * \brief Function pointer type for a callback invoked during hyperspectral data acquisition.
     *
     * \param[in] HyperspectralDataHandle Handle to the acquired hyperspectral data.
     * \param[in] HYPERSPECTRAL_DATA_STATUS Status of the hyperspectral data acquisition.
     * \param[in] HERA_API_STR Null-terminated string containing error message, if any.
	 */
    typedef void(HERA_API_CC* HyperspectralDataCallback)(HyperspectralDataHandle, HYPERSPECTRAL_DATA_STATUS, HERA_API_STR);

    /**
     * \fn HeraAPI_GetLastErrorMessage
     * \details Get the last error occurred in the API. All the other functions in the API return a status code giving information about the execution. In case these functions return any
     * value but zero, or \c HERA_API_OK , this function can be used to get a more details description of the error occurred.
     *
     * \return HERA_API_STR Null-terminated string describing the last occurred error.
     */
    HERA_API_EXP HERA_API_STR HERA_API_CC HeraAPI_GetLastErrorMessage();

    /**
     * \fn HeraAPI_GetVersion(int* major, int* minor, int* build)
     * \details Get API version. This function retrieves the current version number of the HeraAPI. This function provides the major, minor, and build numbers of the version.
     *
     * \param[out] Major version number.
     * \param[out] Minor version number.
     * \param[out] Build number.
     *
     * \return HERA_API_OK on success, error code otherwise.
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetVersion(int* major, int* minor, int* build);

    /**
     * \fn HeraAPI_SetResourceUsage(HeraResourceUsage usage)
     * \details Sets the resource usage level for the API. This function allows the user to specify the desired level of resource consumption (e.g., CPU, memory) by the library.
     * Higher resource usage may improve performance, while lower usage may reduce system impact.
     *
     * \param[in] usage Desired resource usage level.
     *
     * \return HERA_API_STATUS Returns HERA_API_OK on success, or an error code on failure.
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_SetResourceUsage(HeraResourceUsage usage);

    /**
     * \fn HeraAPI_Cleanup()
     * \details Ensures that all the resources allocated internally by the library (devices, live frames, hypercubes, etc.) are correctly released. Note that by using it every
     * handle previously returned by the library will be deallocated and invalidated.
	 * It sets also the default resource usage to \c HeraResourceUsage::Max.
     *
     * \return HERA_API_OK on success, error code otherwise.
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_Cleanup(); // Also invalidates every handle!


    /**
     * \fn HeraAPI_IsLicensed(bool* isLicensed, long long* appliedLicenseExpiryUTC, long long* appliedCertificateExpiryUTC)
     * \brief Informs whether the product is correctly licensed.
     *
     * \param[out] isLicensed True if product is licensed, false otherwise.
     * \param[out] appliedLicenseExpiryUTC Expiration time of applied license (UTC, as UNIX timestamp).
     * \param[out] appliedCertificateExpiryUTC Expiration time of applied certificate (UTC, as UNIX timestamp).
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_IsLicensed(bool* isLicensed, long long* appliedLicenseExpiryUTC, long long* appliedCertificateExpiryUTC);

    /**
     * \fn HeraAPI_ActivateLicenseOnline(HERA_API_STR key)
     * \brief Unlocks the API by applying a licence key. As the name suggests, this function could do an online check.
     *
     * \param[in] key Null-terminated string containing the license key to activate
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_ActivateLicenseOnline(HERA_API_STR key);

    /**
     * \fn HeraAPI_ActivateLicenseOffline(HERA_API_STR key, HERA_API_STR certificate)
     * \brief Unlocks the API by applying an offline certificate.
     *
     * \param[in] key Null-terminated string containing the license key to activate
     * \param[in] certificate Null-terminated certificate string.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_ActivateLicenseOffline(HERA_API_STR key, HERA_API_STR certificate);

    /**
     * \fn HeraAPI_EnumerateDevices(size_t* count)
     * \brief Identifies the connected devices and returns their number. Once the number of devices is known more info about the i-th device can be obtained through
     * the \a HeraAPI_GetDeviceInfoByIndex function.
     *
     * \param[out] count Pointer where the number of connected devices will be stored.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_EnumerateDevices(size_t* count);

    /**
     * \fn HeraAPI_GetDeviceInfoByIndex(size_t index, HeraDeviceInfo* deviceInfo)
     * \details This function retrieves a descriptor of the i-th connected device. The descriptor not only provides information about Product,
     * SerialNumber and Vendor, but can also be used to instantiate a device passing it to the function \c HeraAPI_CreateDevice. The index must be smaller than the value
     * obtained by \c HeraAPI_EnumerateDevices and \c deviceInfo must be passed by reference.
     *
     * \param[in] index Index of the device. Must be smaller than the \c count parameter obtained by \c HeraAPI_EnumerateDevices.
     * \param[out] deviceInfo Pointer to HeraDeviceInfo structure to be filled.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetDeviceInfoByIndex(size_t index, HeraDeviceInfo* deviceInfo);

    /**
     * \fn HeraAPI_GetDeviceInfo(const HeraDeviceHandle deviceHandle, HeraDeviceInfo* deviceInfo)
     * \details This function retrieves a descriptor of the instance of the device referenced by \c deviceHandle. The \c HeraDeviceInfo retrieved
     * contains the following info: Id, Product, SerialNumber and Vendor.
     *
     * \param deviceHandle Valid handle of an instanced device.
     * \param[out] deviceInfo Pointer to \c HeraDeviceInfo to be filled with information about the target device.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetDeviceInfo(const HeraDeviceHandle deviceHandle, HeraDeviceInfo* deviceInfo);

    /**
     * \fn HeraAPI_CreateDevice(const HeraDeviceInfo* deviceInfo, HeraDeviceHandle* deviceHandle)
     * \details The function instantiates the device corresponding to the descriptor obtained by \c HeraAPI_GetDeviceInfoByIndex. If the call is successful the device can be controlled
     * passing the returned device \c deviceHandle to the appropriate functions. When the device is no longer useful you can release the device using \c HeraAPI_ReleaseDevice.
     * Notice that after the release the device is deallocated and the handle invalidated. Any call using that handle can lead to unexpected behaviours.
     *
     * \param deviceInfo Pointer to HeraDeviceInfo obtained by \c HeraAPI_GetDeviceInfoByIndex.
     * \param[out] deviceHandle Handle to the newly created device.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_CreateDevice(const HeraDeviceInfo* deviceInfo, HeraDeviceHandle* deviceHandle);

    /**
     * \fn HeraAPI_ReleaseDevice(const HeraDeviceHandle deviceHandle)
     * \details Deallocates device memory and invalidates the instanced device. This invalidates the handle and should only be used when the device is
     * no longer needed. Notice that after the release any call using that handle can lead to unexpected behaviours.
     *
     * \param deviceHandle \c HeraDeviceHandle of the device to be released.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_ReleaseDevice(const HeraDeviceHandle deviceHandle);

    /**
     * \fn HeraAPI_RestoreDeviceFactoryDefaults(const HeraDeviceHandle deviceHandle)
     * \details This function resets the default factory settings of the device.
     *
     * \param deviceHandle \c HeraDeviceHandle of the device to reset to default settings.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_RestoreDeviceFactoryDefaults(const HeraDeviceHandle deviceHandle);

    /**
     * \fn HeraAPI_Connect(const HeraDeviceHandle deviceHandle)
     * \details Connects an instanced, plugged device. This function is implicitly called by the \c HeraAPI_CreateDevice. Once connected you can
     * start interacting with the device.
     *
     * \param deviceHandle \c HeraDeviceHandle of the instanced device to connect.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_Connect(const HeraDeviceHandle deviceHandle);

    /**
     * \fn HeraAPI_Disconnect(const HeraDeviceHandle deviceHandle)
     * \details Disconnects an instanced, connected device. This function can be used to disconnect the device while it is not used,
     * the same instance can be connected later without creating another device.
     *
     * \param[in] deviceHandle Handle to the device.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_Disconnect(const HeraDeviceHandle deviceHandle);

    /**
     * \fn HeraAPI_IsConnected(const HeraDeviceHandle deviceHandle, bool* connected)
     * \details Interrogates the device to know its connection status.
     *
     * \param[in] deviceHandle Handle to the device.
     * \param[out] connected The returned connection status. True if the device is connected, false otherwise.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_IsConnected(const HeraDeviceHandle deviceHandle, bool* connected);

    /**
     * \fn HeraAPI_RegisterDeviceLostCallback(const HeraDeviceHandle deviceHandle, VoidCallback deviceLostCb)
     * \details Allows to register a function to be called in the event the device is unexpectedly disconnected. The registered function must be a \a void return function taking no parameters. See
     * \c VoidCallback declaration for more details. \a NB: The functionality is not fully implemented and cannot detect the disconnection of every internal component. In particular it is not available
     * for Hera SWIR model. It will soon be fixed in future releases, for now we suggest to replace the \c HeraAPI_RegisterDeviceLostCallback implementing a polling mechanism.
     *
     * \param[in] deviceHandle Handle to the device.
     * \param deviceLostCb Void function taking no parameters to handle device unexpected disconnection. See \c VoidCallback for more details.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_RegisterDeviceLostCallback(const HeraDeviceHandle deviceHandle, VoidCallback deviceLostCb);

    /**
     * \fn HeraAPI_UnregisterDeviceLostCallback(const HeraDeviceHandle deviceHandle)
     * \details Unregister all the callback functions registered on the device for the disconnection. After this call functions previously triggered by device unexpected disconnection
     * won't be called anymore.
     *
     * \param[in] deviceHandle Handle to the device.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_UnregisterDeviceLostCallback(const HeraDeviceHandle deviceHandle);

    /**
     * \fn HeraAPI_RegisterLiveCaptureCallbacks(const HeraDeviceHandle deviceHandle, StrCallback errorCb, IntCallback timeoutCb, LiveCaptureCallback captureCb)
     * \brief Allows to register the functions to be called on live capture related events. The registered functions will handle the live capture error occurrences, timeout, and
     * actual live image capture. This functions can be used to display live images and handle erroneous situations.
     *
     * \param[in] deviceHandle Handle to the device.
     * \param errorCb Void function taking \c HERA_API_STR as parameter, that is a null terminated string containing the occurred error message.
     * \param timeoutCb Void function taking \c int as parameter. This number indicates the number of buffers of the camera free to store live images,
     * if this number is zero it may be an indication that you are not releasing the resources required by the live frames through the function \c HeraAPI_ReleaseLiveCaptureResult.
     * \param captureCb \c GetLiveCaptureHandle the live image handle. This handle can be passed to the function \c HeraAPI_GetLiveCaptureInfo to the actual image data, and information
     * on how to interpret these data such as \c bitPerPixel.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_RegisterLiveCaptureCallbacks(const HeraDeviceHandle deviceHandle, StrCallback errorCb, IntCallback timeoutCb, LiveCaptureCallback captureCb);

    /**
     * \fn HeraAPI_UnregisterLiveCaptureCallbacks(const HeraDeviceHandle deviceHandle)
     * \brief Unregister all the callback functions registered on the device for the live capture. After this call functions previously triggered by live mode errors, and captures
     * won't be called anymore.
     *
     * \param[in] deviceHandle Handle to the device.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_UnregisterLiveCaptureCallbacks(const HeraDeviceHandle deviceHandle);

    /**
     * \fn HeraAPI_RegisterHyperspectralDataAcqCallbacks(const HeraDeviceHandle deviceHandle, FloatCallback progressCb, HyperspectralDataCallback dataAcquiredCb)
     * \brief Allows to register the functions to be called during hyperspectral acquisition execution and when it's terminated (correctly or not). The registered functions will handle
     * the tracking of the acquisition progress and will handle the acquired data or the eventual error in acquisition.
     * \param[in] deviceHandle Handle to the device.
     * \param progressCb Void function taking \c float as parameter, that is the state of the progress from 0 to 1. This function can be used to track progress.
     * \param dataAcquiredCb Void function taking three parameters: \c HyperspectralDataHandle, this handles the raw hyperspectral data to be processed to get the hypercube
     * using the function \c HeraAPI_GetHyperCube, information can be extracted passing the handle to \c HeraAPI_GetHyperspectralDataInfo. \c HYPERSPECTRAL_DATA_STATUS is an enumeration
     * specifying the state of the outcome, that is whether the acquisition was successful or not. \c HERA_API_STR is a null terminated string containing the error message, in
     * case an error occurred.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_RegisterHyperspectralDataAcqCallbacks(const HeraDeviceHandle deviceHandle, FloatCallback progressCb, HyperspectralDataCallback dataAcquiredCb);

    /**
     * \fn HeraAPI_UnregisterHyperspectralDataAcqCallbacks(const HeraDeviceHandle deviceHandle)
     * \brief Unregister all the callback functions registered on the device for hyperspectral data acquisition. After this call, functions previously triggered by hyperspectral acquisition completion
     * won't be called anymore.
     *
     * \param[in] deviceHandle Handle to the device.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_UnregisterHyperspectralDataAcqCallbacks(const HeraDeviceHandle deviceHandle);

    /**
     * \fn HeraAPI_GetGainLevelResolution(const HeraDeviceHandle deviceHandle, double* gainLevelResolution)
     * \brief Get the minimum increment of the camera gain level. Gain level transposes possible gain values on the range 0-1.
     *
     * \param[in] deviceHandle Handle to the device.
     * \param[out] gainLevelResolution The output value is a \c double. Being the increment of a 0 to 1 value its range is 0-1 itself, zero excluded.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetGainLevelResolution(const HeraDeviceHandle deviceHandle, double* gainLevelResolution);

    /**
     * \fn HeraAPI_GetGainLevel(const HeraDeviceHandle deviceHandle, double* gainLevel)
     * \details Get the current gain level of the camera ranging from 0 to 1, with 0 representing the minimum dB level of the actual parameter and 1 indicating the maximum.
     *
     * \param[in] deviceHandle Handle to the device.
     * \param[out] gainLevel The output value is a \c double. Being the increment of a 0 to 1 value its range is 0-1 itself, zero excluded.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetGainLevel(const HeraDeviceHandle deviceHandle, double* gainLevel);

    /**
     * \fn HeraAPI_SetGainLevel(const HeraDeviceHandle deviceHandle, double gainLevel)
     * \brief Set the current gain level of the camera ranging from 0 to 1, with 0 representing the minimum dB level of the actual parameter and 1 indicating the maximum. If the value doesn't
     * match a valid level it will be rounded to the closest valid value, while for out of range values an error status will be returned.
     *
     * \param[in] deviceHandle Handle to the device.
     * \param gainLevel \c Double value of the gain level you want to set. Must be between 0 and 1.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_SetGainLevel(const HeraDeviceHandle deviceHandle, double gainLevel);

    /**
     * \fn HeraAPI_GetActualGain(const HeraDeviceHandle deviceHandle, double* gain)
     * \brief Get the current digital gain of the camera in dB.
     * \param[in] deviceHandle Handle to the device.
     * \param[out] gain The output value is a \c double.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetActualGain(const HeraDeviceHandle deviceHandle, double* gain);

    /**
     * \fn HeraAPI_IsGainLevelWritable(const HeraDeviceHandle deviceHandle, bool* isWritable)
     * \brief Verifies whether the gain level of the camera can be modified by the user.
     *
     * This function checks if the current device allows the user to set or modify the gain level parameter.
     *
     * \param[in] deviceHandle Handle to the device.
     * \param[out] isWritable Pointer to a bool that will be set to true if the gain level can be modified, false otherwise.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_IsGainLevelWritable(const HeraDeviceHandle deviceHandle, bool* isWritable);

    /**
     * \fn HeraAPI_SetExposure(const HeraDeviceHandle deviceHandle, double exposure_us)
     * \brief Set the current exposure time of the camera in microseconds.
     * \param[in] deviceHandle Handle to the device.
     * \param[in] exposure_us \c Double value of the desired camera exposure in micro seconds [µs].
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_SetExposure(const HeraDeviceHandle deviceHandle, double exposure_us);
    /**
     * \fn HeraAPI_GetExposure(const HeraDeviceHandle deviceHandle, double* exposure_us)
     * \brief Get the current exposure time of the camera in microseconds.
     * \param[in] deviceHandle Handle to the device.
     * \param[out] exposure_us \c Double value of the current camera exposure in micro seconds [µs].
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetExposure(const HeraDeviceHandle deviceHandle, double* exposure_us);

    /**
     * \fn HeraAPI_IsExposureWritable(const HeraDeviceHandle deviceHandle, bool* isWritable)
     * \brief Verifies whether the exposure time of the camera can be modified by the user.
     *
     * This function checks if the current device allows the user to set or modify the exposure time parameter.
     *
     * \param[in] deviceHandle Handle to the device.
     * \param[out] isWritable Pointer to a bool that will be set to true if the exposure time can be modified, false otherwise.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_IsExposureWritable(const HeraDeviceHandle deviceHandle, bool* isWritable);

    /**
     * \fn HeraAPI_IsBlackLevelSetSupported(const HeraDeviceHandle deviceHandle, bool* isSupported)
     * \brief This function provides information about the availability of the black level feature.
     * \param[in] deviceHandle Handle to the device.
     * \param[out] isSupported True if black level is available, false otherwise.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_IsBlackLevelSetSupported(const HeraDeviceHandle deviceHandle, bool* isSupported);

    /**
     * \fn HeraAPI_SetBlackLevel(const HeraDeviceHandle deviceHandle, double blackLevel)
     * \brief Set the current black level offset of the camera. It fails if black level isn't supported.
     * \param[in] deviceHandle Handle to the device.
     * \param[in] blackLevel \c Double value of the desired camera black level offset.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_SetBlackLevel(const HeraDeviceHandle deviceHandle, double blackLevel);
    
    /**
     * \fn HeraAPI_GetBlackLevel(const HeraDeviceHandle deviceHandle, double* blackLevel)
     * \brief Get the current camera black level offset.
     * \param[in] deviceHandle Handle to the device.
     * \param[out] blackLevel \c Double value of the current camera black level offset.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetBlackLevel(const HeraDeviceHandle deviceHandle, double* blackLevel);

    /**
     * \fn HeraAPI_IsBlackLevelWritable(const HeraDeviceHandle deviceHandle, bool* isWritable)
     * \brief Verifies whether the black level of the camera can be modified by the user.
     *
     * This function checks if the current device allows the user to set or modify the black level offset.
     *
     * \param[in] deviceHandle Handle to the device.
     * \param[out] isWritable Pointer to a bool that will be set to true if the black level can be modified, false otherwise.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_IsBlackLevelWritable(const HeraDeviceHandle deviceHandle, bool* isWritable);

    /**
     * \fn HeraAPI_SetROI(const HeraDeviceHandle deviceHandle, unsigned int offsetX, unsigned int offsetY, unsigned int width, unsigned int height)
     * \brief Sets the current Region Of Interest of the camera sensor.
     * \param[in] deviceHandle Handle to the device.
     * \param[in] offsetX Upper left angle horizontal offset in pixels.
     * \param[in] offsetY Upper left angle vertical offset in pixels.
     * \param[in] width ROI width in pixels.
     * \param[in] height ROI height in pixels.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_SetROI(const HeraDeviceHandle deviceHandle, unsigned int offsetX, unsigned int offsetY, unsigned int width, unsigned int height);
    
    /**
     * \fn HeraAPI_ClearROI(const HeraDeviceHandle deviceHandle)
     * \brief Resets the default Region Of Interest of the camera sensor.
     * \param[in] deviceHandle Handle to the device.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_ClearROI(const HeraDeviceHandle deviceHandle);

    /**
     * \fn HeraAPI_GetOffsetX(const HeraDeviceHandle deviceHandle, unsigned int* offsetX)
     * \brief Get the value of the current camera ROI upper left corner horizontal offset in pixels.
     * \param[in] deviceHandle Handle to the device.
     * \param[out] offsetX Value of the current ROI upper-left corner horizontal offset.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetOffsetX(const HeraDeviceHandle deviceHandle, unsigned int* offsetX);
    /**
     * \fn HeraAPI_GetOffsetY(const HeraDeviceHandle deviceHandle, unsigned int* offsetY)
     * \brief Get the value of the current camera ROI upper left corner vertical offset in pixels.
     *
     * \param[in] deviceHandle Handle to the device.
     * \param[out] offsetY Value of the current ROI upper-left corner vertical offset.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetOffsetY(const HeraDeviceHandle deviceHandle, unsigned int* offsetY);
    /**
     * \fn HeraAPI_GetWidth(const HeraDeviceHandle deviceHandle, unsigned int* width)
     * \brief Get the value of the current camera ROI width in pixels.
     *
     * \param[in] deviceHandle Handle to the device.
     * \param[out] width Value of the current ROI width.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetWidth(const HeraDeviceHandle deviceHandle, unsigned int* width);
    /**
     * \fn HeraAPI_GetHeight(const HeraDeviceHandle deviceHandle, unsigned int* height)
     * \brief Get the value of the current camera ROI height in pixels.
     *
     * \param[in] deviceHandle Handle to the device.
     * \param[out] height Value of the current ROI height.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetHeight(const HeraDeviceHandle deviceHandle, unsigned int* height);

    /**
     * \fn HeraAPI_IsROIWritable(const HeraDeviceHandle deviceHandle, bool* isWritable)
     * \brief Verifies whether the Region Of Interest (ROI) of the camera can be modified by the user.
     *
     * This function checks if the current device allows the user to set or modify the ROI parameters (position and size).
     *
     * \param[in] deviceHandle Handle to the device.
     * \param[out] isWritable Pointer to a bool that will be set to true if the ROI can be modified, false otherwise.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_IsROIWritable(const HeraDeviceHandle deviceHandle, bool* isWritable);

    /**
     * \fn HeraAPI_GetTemperatureInfo(const HeraDeviceHandle deviceHandle, HeraTemperatureStatus* temperatureStatus, double* temperature)
     * \brief Get status and value of the device temperature, if supported. Availability of the feature can be inferred from the temperature status.
     *
     * \param[in] deviceHandle Handle to the device.
     * \param[out] temperatureStatus Pointer to the enumeration \c HeraTemperatureStatus about the state of the camera temperature in terms of security. Also has a special value
     * if temperature information is not available.
     * \param[out] temperature Value of the current camera sensor temperature.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetTemperatureInfo(const HeraDeviceHandle deviceHandle, HeraTemperatureStatus* temperatureStatus, double* temperature);

    /**
     * \fn HeraAPI_IsPixelFormatSupported(const HeraDeviceHandle deviceHandle, HeraPixelFormat pixelFormat, bool* supported)
     * \brief Checks if a pixel format is supported by the camera when HDR is disabled.
     * 
     * This function indicates whether the specified pixel format is supported by the camera in non-HDR mode. It is equivalent to calling 
     * HeraAPI_IsPixelFormatSupportedEx with @p hdr set to false.
     *
     * \param[in] deviceHandle Handle to the device.
     * \param[in] pixelFormat \c HeraPixelFormat whose availability you want to verify.
     * \param[out] supported Pointer to \c bool. If \c pixelFormat is supported, true is returned. False otherwise.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_IsPixelFormatSupported(const HeraDeviceHandle deviceHandle, HeraPixelFormat pixelFormat, bool* supported);

    /**
     * \fn HeraAPI_IsPixelFormatSupportedEx(const HeraDeviceHandle deviceHandle, HeraPixelFormat pixelFormat, bool hdr, bool* supported)
     * \brief Checks if a pixel format is supported by the camera when HDR is disabled.
     *
     * This function checks whether the specified pixel format is supported by the camera, with the ability to explicitly specify HDR or non-HDR state.
     *
     * \param[in] deviceHandle Handle to the device.
     * \param[in] pixelFormat \c HeraPixelFormat whose availability you want to verify.
     * \param[in] hdr Indicates whether to check support in HDR mode (true = HDR, false = non-HDR).
     * \param[out] supported Pointer to \c bool. If \c pixelFormat is supported, true is returned. False otherwise.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_IsPixelFormatSupportedEx(const HeraDeviceHandle deviceHandle, HeraPixelFormat pixelFormat, bool hdr, bool* supported);

    /**
     * \fn HeraAPI_IsHDRSupported(const HeraDeviceHandle deviceHandle, bool* supported)
     * \brief Get information about the availability of the HDR.
     *
     * \param[in] deviceHandle Handle to the device.
     * \param[out] supported Pointer to \c bool. If HDR is supported, true is returned. False otherwise.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_IsHDRSupported(const HeraDeviceHandle deviceHandle, bool* supported);

    /**
     * \fn HeraAPI_SetHDR(const HeraDeviceHandle deviceHandle, bool hdr)
     * \brief Enable or disable the camera HDR mode.
     * \param[in] deviceHandle Handle to the device.
     * \param[in] hdr The desired HDR mode.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_SetHDR(const HeraDeviceHandle deviceHandle, bool hdr);

    /**
     * \fn HeraAPI_GetHDR(const HeraDeviceHandle deviceHandle, bool* hdr)
     * \brief Get the current camera HDR mode.
     * \param[in] deviceHandle Handle to the device.
     * \param[out] hdr \c bool value of the current HDR mode.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetHDR(const HeraDeviceHandle deviceHandle, bool* hdr);

    /**
     * \fn HeraAPI_StartLiveCapture(const HeraDeviceHandle deviceHandle, HeraPixelFormat pixelFormat)
     * \brief Start live images acquisition using the specified pixel format. If the pixel format is not supported the default camera format will be used instead.
     * \param[in] deviceHandle Handle to the device.
     * \param[in] pixelFormat The desired \c HeraPixelFormat for the live acquisition. Specifies the number of bits representing each pixel.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_StartLiveCapture(const HeraDeviceHandle deviceHandle, HeraPixelFormat pixelFormat);
    /**
     * \fn HeraAPI_StartLiveCaptureEx(const HeraDeviceHandle deviceHandle, HeraPixelFormat pixelFormat, int* saturationThreshold)
     * \brief Start live images acquisition using the specified pixel format. If the pixel format is not supported the default camera format will be used instead.
     * \param[in] deviceHandle Handle to the device.
     * \param[in] pixelFormat The desired \c HeraPixelFormat for the live acquisition. Specifies the number of bits representing each pixel.
     * \param[out] saturationThreshold Saturation level of the hyperspectral camera given the specified pixelFormat.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_StartLiveCaptureEx(const HeraDeviceHandle deviceHandle, HeraPixelFormat pixelFormat, int* saturationThreshold);
    /**
     * \fn HeraAPI_StopLiveCapture(const HeraDeviceHandle deviceHandle)
     *
     * \brief Stop the acquisition of live frames.
     *
     * \param[in] deviceHandle Handle to the device.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_StopLiveCapture(const HeraDeviceHandle deviceHandle);

    /**
     * \fn HeraAPI_IsLiveCapturing(const HeraDeviceHandle deviceHandle, bool* isLiveCapturing)
     * \brief Interrogate the camera on whether it is currently acquiring live images or not.
     * \param[in] deviceHandle Handle to the device.
     * \param[out] isLiveCapturing Pointer to \c bool. If the camera is acquiring live frames, true is returned. False otherwise.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_IsLiveCapturing(const HeraDeviceHandle deviceHandle, bool* isLiveCapturing);

    /**
     * \fn HeraAPI_IsTriggerModeSupported(const HeraDeviceHandle deviceHandle, HeraTriggerMode triggerMode, bool* supported)
     * \brief Predicate method used to know if a \c HeraTriggerMode is supported by the camera.
     * \param[in] deviceHandle
     * \param[in] triggerMode \c HeraTriggerMode, that is an enumeration that determines the kind of trigger to be used for the scan.
     * \param[out] supported Pointer to \c bool. If \c triggerMode is supported, true is returned. False otherwise.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_IsTriggerModeSupported(const HeraDeviceHandle deviceHandle, HeraTriggerMode triggerMode, bool* supported);

    /**
     * \fn HeraAPI_IsExternalTriggerDelaySupported(const HeraDeviceHandle deviceHandle, bool* supported)
     * \brief Predicate method used to know if an external trigger delay is supported by the camera.
     * \param[in] deviceHandle
     * \param[out] supported Pointer to \c bool. If an external trigger delay is supported, true is returned. False otherwise.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_IsExternalTriggerDelaySupported(const HeraDeviceHandle deviceHandle, bool* supported);

    /**
     * \fn HeraAPI_SetExternalTriggerDelay(const HeraDeviceHandle deviceHandle, double delay_us)
     * \brief Set the external trigger delay in microseconds.
     * \param[in] deviceHandle Handle to the device.
     * \param[in] delay_us \c Double value of the desired external trigger delay in micro seconds [µs].
     *
     * \return HERA_API_STATUS
	 */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_SetExternalTriggerDelay(const HeraDeviceHandle deviceHandle, double delay_us);

    /**
     * \fn HeraAPI_GetExternalTriggerDelay(const HeraDeviceHandle deviceHandle, double* delay_us)
     * \brief Get the current external trigger delay in microseconds.
     * \param[in] deviceHandle Handle to the device.
     * \param[out] delay_us \c Double value of the current external trigger delay in micro seconds [µs].
     *
     * \return HERA_API_STATUS
	 */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetExternalTriggerDelay(const HeraDeviceHandle deviceHandle, double* delay_us);

    /**
     * \fn HeraAPI_IsScanModeSupported(const HeraDeviceHandle deviceHandle, HeraScanMode scanMode, bool* supported)
     * \brief Predicate method used to know if a \c HeraScanMode is supported by the camera.
     * \param[in] deviceHandle Handle to the device.
     * \param[in] scanMode \c HeraScanMode, that is an enumeration that determines the kind of hyperspectral acquisition to be performed.
     * \param[out] supported Pointer to \c bool. If \c scanMode is supported, true is returned. False otherwise.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_IsScanModeSupported(const HeraDeviceHandle deviceHandle, HeraScanMode scanMode, bool* supported);

    /**
     * \fn HeraAPI_GetSupportedScanModes(const HeraDeviceHandle deviceHandle, HeraScanModeVec* scanModes, size_t* count)
     * \brief Get the list of the available scan modes for the device.
     *
     * \param[in] deviceHandle Handle to the device.
     * \param[out] scanModes Pointer to \c HeraScanModeVec. The given array will be filled with the list of available scan modes.
     * \param[out] count Number of elements in the output \c HeraScanModeVec array.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetSupportedScanModes(const HeraDeviceHandle deviceHandle, HeraScanModeVec* scanModes, size_t* count);

    /**
     * \fn HeraAPI_GetDefaultOutBands(const HeraDeviceHandle deviceHandle, HeraScanMode scanMode, int* outBands)
     * \brief Retrieve the default number of output bands for the specified scan mode.
     *
     * \param[in] deviceHandle Handle to the device.
     * \param[in] scanMode \c HeraScanMode, that is an enumeration that determines the kind of hyperspectral acquisition to be performed.
     * \param[out] outBands Number of output bands for the specified scan mode.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetDefaultOutBands(const HeraDeviceHandle deviceHandle, HeraScanMode scanMode, int* outBands);

    /**
     * \fn HeraAPI_StartHyperspectralDataAcquisition(const HeraDeviceHandle deviceHandle, HeraScanMode scanMode, int averages, int stabilizationTimeMs)
     * \brief Start the acquisition of hyperspectral data with the desired \c scanMode, \c averages, \c stabilizationTime.
     * \param[in] deviceHandle Handle to the device.
     * \param[in] scanMode \c HeraScanMode determines the kind of acquisition to be performed, the longer the acquisition the higher the spectral resolution.
     * \param[in] averages \c int value that determines how many images will be averaged at each step of the acquisition. This impacts linearly the duration of the measurement but reduces
     * the noise in the outcome.
     * \param[in] stabilizationTime \c int value that determines the number of milliseconds to wait between each acquisition step. Steps imply small mechanical movements that can disturb the
     * acquisition in particularly delicate setups. However this setting is rarely necessary and should be kept to zero if not really needed.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_StartHyperspectralDataAcquisition(const HeraDeviceHandle deviceHandle, HeraScanMode scanMode, int averages, int stabilizationTimeMs);

    /**
     * \fn HeraAPI_StartHyperspectralDataAcquisitionEx(const HeraDeviceHandle deviceHandle, HeraScanMode scanMode, HeraTriggerMode triggerMode, int averages, int stabilizationTimeMs)
     * \brief Start the acquisition of hyperspectral data with the desired \c scanMode, \c triggerMode, \c averages, \c stabilizationTime.
     * \param[in] deviceHandle
     * \param[in] scanMode \c HeraScanMode determines the kind of acquisition to be performed, the longer the acquisition the higher the spectral resolution.
     * \param[in] triggerMode \c HeraTriggerMode determines the kind of trigger to be used for the scan.
     * \param[in] averages \c int value that determines how many images will be averaged at each step of the acquisition. This impacts linearly the duration of the measurement but reduces
     * the noise in the outcome.
     * \param[in] stabilizationTime \c int value that determines the number of milliseconds to wait between each acquisition step. Steps imply small mechanical movements that can disturb the
     * acquisition in particularly delicate setups. However this setting is rarely necessary and should be kept to zero if not really needed.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_StartHyperspectralDataAcquisitionEx(const HeraDeviceHandle deviceHandle, HeraScanMode scanMode, HeraTriggerMode triggerMode, int averages, int stabilizationTimeMs);

    /**
     * \fn HeraAPI_AbortHyperspectralDataAcquisition(const HeraDeviceHandle deviceHandle)
     * \brief This function aborts the hyperspectral data acquisition before it is completed.
     * \param[in] deviceHandle Handle to the device.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_AbortHyperspectralDataAcquisition(const HeraDeviceHandle deviceHandle);
    
    /**
     * \fn HeraAPI_IsAcquiringHyperspectralData(const HeraDeviceHandle deviceHandle, bool* isAcquiringHyperspectralData)
     * \brief Interrogate the camera on whether it is currently acquiring hyperspectral data or not.
     * \param[in] deviceHandle Handle to the device.
     * \param[out] isAcquiringHyperspectralData Pointer to \c bool. If the camera is acquiring hyperspectral data, true is returned. False otherwise.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_IsAcquiringHyperspectralData(const HeraDeviceHandle deviceHandle, bool* isAcquiringHyperspectralData);

    // HyperCube methods
    /**
     * \fn HeraAPI_GetHyperCube(const HeraDeviceHandle deviceHandle, const HyperspectralDataHandle dataHandle, HyperCubeHandle* hyperCubeHandle, HeraDataType dataType, HeraBinningFactor binning, FloatCallback processCallback = nullptr)
     * \brief This function is meant to be used at the end of a successful hyperspectral acquisition and requires the hyperspectral data passed to the callback function registered by
     * \c HeraAPI_RegisterHyperspectralDataAcqCallbacks as \c dataAcquiredCb. It computes the Fourier Transform of the acquired data to get the hyperspectral cube.
     *
     * \param[in] deviceHandle Handle to the device.
     * \param[in] dataHandle \c HyperspectralDataHandle obtained as parameter of the callback function \c dataAcquiredCb.
     * \param[out] hyperCubeHandle Pointer to the computed hypercube.
     * \param[in] dataType Specify the \c HeraDataType desired for the output cube. This will affect the size of the outcome.
     * \param[in] binning \c HeraBinningFactor enum indicates the binning strategy to use computing the FT. This will affect computation time.
     * \param[in] processCallback Void callback function taking a \c float as parameter. This optional function is used to track computation progress.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetHyperCube(const HeraDeviceHandle deviceHandle, const HyperspectralDataHandle dataHandle, HyperCubeHandle* hyperCubeHandle, HeraDataType dataType, HeraBinningFactor binning, FloatCallback processCallback HERA_API_DEFAULT_NULL);

    /**
    * \fn HeraAPI_GetHyperCubeExtended(const HeraDeviceHandle deviceHandle, const HyperspectralDataHandle dataHandle, HyperCubeHandle* hyperCubeHandle, HeraDataType dataType, unsigned int bandsCount, HeraBinningFactor binning, FloatCallback processCallback HERA_API_DEFAULT_NULL)
    * \brief [DEPRECATED] Use \c HeraAPI_GetHyperCubeEx instead.
    *
    * \deprecated This function is deprecated. It has the same parameters and behavior as \c HeraAPI_GetHyperCubeEx.
    * Please refer to the documentation of \c HeraAPI_GetHyperCubeEx for details.
    *
    * \return HERA_API_STATUS
    */
    DEPRECATED HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetHyperCubeExtended(const HeraDeviceHandle deviceHandle, const HyperspectralDataHandle dataHandle, HyperCubeHandle* hyperCubeHandle, HeraDataType dataType, unsigned int bandsCount, HeraBinningFactor binning, FloatCallback processCallback HERA_API_DEFAULT_NULL);

    /**
     * \fn HeraAPI_GetHyperCubeEx(const HeraDeviceHandle deviceHandle, const HyperspectralDataHandle dataHandle, HyperCubeHandle* hyperCubeHandle, HeraDataType dataType, unsigned int bandsCount, HeraBinningFactor binning, FloatCallback processCallback HERA_API_DEFAULT_NULL)
     * \brief This function is meant to be used at the end of a successful hyperspectral acquisition and requires the hyperspectral data passed to the callback function registered by
     * \c HeraAPI_RegisterHyperspectralDataAcqCallbacks as \c dataAcquiredCb. It computes the Fourier Transform of the acquired data to get the hyperspectral cube.
     *
     * \param[in] deviceHandle Handle to the device.
     * \param[in] dataHandle \c HyperspectralDataHandle obtained as parameter of the callback function \c dataAcquiredCb.
     * \param[out] hyperCubeHandle Pointer to the computed hypercube.
     * \param[in] dataType Specify the \c HeraDataType desired for the output cube. This will affect the size of the outcome.
     * \param[in] bandsCount Specify the desired number of bands for the output cube.
     * \param[in] binning \c HeraBinningFactor enum indicates the binning strategy to use computing the FT. This will affect computation time.
     * \param[in] processCallback Void callback function taking a \c float as parameter. This optional function is used to track computation progress.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetHyperCubeEx(const HeraDeviceHandle deviceHandle, const HyperspectralDataHandle dataHandle, HyperCubeHandle* hyperCubeHandle, HeraDataType dataType, unsigned int bandsCount, HeraBinningFactor binning, FloatCallback processCallback HERA_API_DEFAULT_NULL);

    /**
     * \fn HeraAPI_GetHyperCubeInfo(const HyperCubeHandle hyperCubeHandle, int* width, int* height, int* bands, HeraDataType* dataType)
     * \brief Use this function to extract information about the hypercube from the \c HyperCubeHandle computed by \c HeraAPI_GetHyperCube.
     *
     * \param[in] hyperCubeHandle Handle of the hypercube obtained by \c HeraAPI_GetHyperCube you want to extract information about.
     * \param[out] width Width of each band image of the returned hypercube.
     * \param[out] height Height of each band image of the returned hypercube.
     * \param[out] bands Number of bands of the returned hypercube.
     * \param[out] dataType \c HeraDataType of each image pixel in the returned hypercube.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetHyperCubeInfo(const HyperCubeHandle hyperCubeHandle, int* width, int* height, int* bands, HeraDataType* dataType);

    /**
    * \fn HeraAPI_GetHyperCubeIsHDR(const HyperCubeHandle hyperCubeHandle, bool* isHDR);
    * \brief Use this function to extract information about the HDR mode of the camera during the Hyperspectral data acquisition
    *
    * \param[in] hyperCubeHandle Handle of the hypercube obtained by \c HeraAPI_GetHyperCube you want to extract information about.
    * \param[out] isHDR HDR mode of the camera during the Hyperspectral data acquisition.
    *
    * \return HERA_API_STATUS
    */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetHyperCubeIsHDR(const HyperCubeHandle hyperCubeHandle, bool* isHDR);

    /**
    * \fn HeraAPI_GetHyperCubeBandData(const HyperCubeHandle hyperCubeHandle, unsigned int bandIndex, double* wavelength, void** data)
    * \brief Get the bidimensional image associated to the i-th wavelength of the cube.
    * \param[in] hyperCubeHandle Handle of the hypercube obtained by \c HeraAPI_GetHyperCube you want to extract the wavelength image from.
    * \param[in] bandIndex Integer index of the wavelength you want to extract.
    * \param[out] wavelength Wavelength of the target band index.
    * \param[out] data Pointer to a preallocated memory to be filled with the target band image.
    *
    * \return HERA_API_STATUS
    */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetHyperCubeBandData(const HyperCubeHandle hyperCubeHandle, unsigned int bandIndex, double* wavelength, void** data);

    /**
     * \fn HeraAPI_ExportHyperCubeAsEnvi(const HyperCubeHandle hyperCubeHandle, HERA_API_STR outPath, HERA_API_STR description = nullptr)
     * \brief Function to export hyperspectral image to \c outPath as a .envi file.
     * \param[in] hyperCubeHandle
     * \param[in] outPath Null terminated string containing an existing output path to save the file.
     * \param[in] description Optional textual description of the measurement you are exporting.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_ExportHyperCubeAsEnvi(const HyperCubeHandle hyperCubeHandle, HERA_API_STR outPath, HERA_API_STR description HERA_API_DEFAULT_NULL);

    /**
    * \fn HeraAPI_ExportHyperCubeAsEnviAsync(const HyperCubeHandle hyperCubeHandle, HERA_API_STR outPath, HERA_API_STR description, ProgressCallback progressCallback)
    * \brief Asynchronously exports a hyperspectral image to \c outPath as a .envi file, reporting progress through a callback.
    *
    * This function starts the export operation in a background thread and returns immediately.
    * The \c progressCallback is periodically called with the current progress as a fraction (0.0 to 1.0).
    * The callback can also signal cancellation by returning a negative value.
    *
    * \param[in] hyperCubeHandle Handle to the hyperspectral cube to export.
    * \param[in] outPath Null-terminated string specifying the output file path.
    * \param[in] description Optional textual description of the measurement being exported.
    * \param[in] progressCallback Callback function to receive progress updates and optionally cancel the operation.
    * \param[in] callbackUserData User-defined pointer that is passed to \c progressCallback for context.
    *
    * \return HERA_API_STATUS Status code indicating if the export was successfully started.
    */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_ExportHyperCubeAsEnviAsync(const HyperCubeHandle hyperCubeHandle, HERA_API_STR outPath, HERA_API_STR description, ProgressCallback progressCallback, void* callbackUserData);

    /**
     * \fn HeraAPI_ReleaseHyperCube(const HyperCubeHandle hyperCubeHandle)
     * \brief Release memory and resources associated to the hyperCube. Note that the data referred by the pointer \c data returned by \c HeraAPI_GetHyperCubeInfo will no
     * longer be valid after this call.
     *
     * \param[in] hyperCubeHandle
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_ReleaseHyperCube(const HyperCubeHandle hyperCubeHandle);

    /**
     * \fn HeraAPI_GetLiveCaptureInfo(const LiveCaptureHandle captureHandle, int* width, int* height, int* bitDepth, int* bitPerPixel, int* saturationThreshold, int* rowStride, void** data)
     * \brief Use this function to extract information about the \c LiveCaptureHandle taken as parameter by the callback function registerd as \c captureCb by the
     * function \c HeraAPI_RegisterLiveCaptureCallbacks.
     *
     * \param[in] captureHandle Handle of the live capture you want to extract information from.
     * \param[out] width Width of the live frame.
     * \param[out] height Height of the live frame.
     * \param[out] bitDepth Bit depth of each pixel of the live frame. Bit depth is the number of bits that actually contain information.
     * \param[out] bitPerPixel Number of bit used to store each pixel of the live frame. Bit per pixel is the number of bits physically occupied by a pixel.
     * \param[out] saturationThreshold Saturation level of the hyperspectral camera for the current pixel format.
     * \param[out] rowStride Row stride of the live frame. It's the number of bytes used to store each row of the image in memory.
     * \param[out] data Pointer to a preallocated memory to be filled with the target live frame data.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetLiveCaptureInfo(const LiveCaptureHandle captureHandle, int* width, int* height, int* bitDepth, int* bitPerPixel, int* saturationThreshold, int* rowStride, void** data);
    
    /**
     * \fn HeraAPI_GetLiveCaptureIsHDR(const LiveCaptureHandle captureHandle, bool* isHDR)
     * \brief Use this function to extract information about the HDR mode of the camera during the live capture
     *
     * \param[in] captureHandle Handle of the live capture you want to extract information from.
     * \param[out] isHDR HDR mode of the camera during the live capture.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetLiveCaptureIsHDR(const LiveCaptureHandle captureHandle, bool* isHDR);

    /**
     * \fn HeraAPI_ReleaseLiveCaptureResult(const LiveCaptureHandle captureHandle)
     * \brief Release memory and resources associated to the live image. Note that the data referred by the pointer \c data returned by \c HeraAPI_GetLiveCaptureInfo will no
     * longer be valid after this call. You will need to copy the data if you need them after the release. Note that release is needed to free the camera buffer and keep capturing live
     * images.
     *
     * \param[in] captureHandle
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_ReleaseLiveCaptureResult(const LiveCaptureHandle captureHandle);

    // Hyperspectral data methods
    /**
     * \fn HeraAPI_ExportHyperspectralData(const HyperspectralDataHandle dataHandle, HERA_API_STR outPath, bool compressed)
     * \brief Function to export unprocessed hyperspectral data, optionally compressed.
     *
     * \param[in] dataHandle Handle to the hyperspectral data to be processed.
     * \param[in] outPath Null terminated string containing an existing output path to save the file.
     * \param[in] compressed Bool parameter true if you need the data to be compressed.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_ExportHyperspectralData(const HyperspectralDataHandle dataHandle, HERA_API_STR outPath, bool compressed);

    /**
     * \fn HeraAPI_GetHyperspectralDataInfo(const HyperspectralDataHandle dataHandle, int* width, int* height, void** data)
     * \brief  Use this function to extract information about the \c HyperspectralDataHandle taken as parameter by the callback function registerd as \c dataAcquiredCb by the
     * function \c HeraAPI_RegisterHyperspectralDataAcqCallbacks.
     *
     * \param[in] dataHandle Handle to the hyperspectral data to be processed.
     * \param[out] width Width of the hyperspectral frame.
     * \param[out] height Height of the hyperspectral frame.
     * \param[out] data Pointer to a preallocated memory to be filled with the actual hyperspectral data.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetHyperspectralDataInfo(const HyperspectralDataHandle dataHandle, int* width, int* height, void** data);

    /**
     * \fn HeraAPI_GetHyperspectralDataIsHDR(const HyperspectralDataHandle dataHandle, bool* isHDR);
     * \brief Use this function to extract information about the HDR mode of the camera during the Hyperspectral data acquisition
     *
     * \param[in] dataHandle Handle to the hyperspectral data to be processed.
     * \param[out] isHDR HDR mode of the camera during the Hyperspectral data acquisition
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_GetHyperspectralDataIsHDR(const HyperspectralDataHandle dataHandle, bool* isHDR);
    
    /**
     * \fn HeraAPI_ReleaseHyperspectralData(const HyperspectralDataHandle dataHandle)
     * \brief Release memory and resources associated to the hyperspectral data. Note that the data referred by the pointer \c data returned by \c HeraAPI_GetHyperspectralDataInfo will no
     * longer be valid after this call.
     *
     * \param[in] dataHandle Handle to the hyperspectral data to be released.
     *
     * \return HERA_API_STATUS
     */
    HERA_API_EXP HERA_API_STATUS HERA_API_CC HeraAPI_ReleaseHyperspectralData(const HyperspectralDataHandle dataHandle);

HERA_API_EXTERNC_END