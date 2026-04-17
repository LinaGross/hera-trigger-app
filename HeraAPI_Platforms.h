#pragma once

#if defined(__cplusplus)
	#define HERA_API_EXTERNC_BEG extern "C" {
	#define HERA_API_EXTERNC_END }
	#define HERA_API_DEFAULT_NULL = nullptr
#else
	#define HERA_API_EXTERNC_BEG
	#define HERA_API_EXTERNC_END
	#define HERA_API_DEFAULT_NULL
#endif

#ifdef __cplusplus
	#include <cstdint>
#else
	#if defined(__STDC_VERSION__) && __STDC_VERSION__ >= 199901L
	#include <stdint.h>
	#include <stdbool.h>
#else
	typedef signed char        int8_t;
	typedef short              int16_t;
	typedef int                int32_t;
	typedef long long          int64_t;
	typedef unsigned char      uint8_t;
	typedef unsigned short     uint16_t;
	typedef unsigned int       uint32_t;
	typedef unsigned long long uint64_t;
	#ifndef bool
		#define bool _Bool
		#define true 1
		#define false 0
	#endif
#endif
#endif

#if defined _WIN64
	#ifndef _SIZE_T_DEFINED
		typedef unsigned long long size_t;
	#endif
#elif defined _WIN32 || defined __CYGWIN__
	#ifndef _SIZE_T_DEFINED
		typedef unsigned int size_t;
	#endif
#else
	typedef __SIZE_TYPE__ size_t;
#endif

#if defined _WIN32 || defined __CYGWIN__
	#define DEPRECATED __declspec(deprecated)
#elif defined(__linux__)
	#define DEPRECATED __attribute__((deprecated))
#elif defined(__APPLE__)
	#define DEPRECATED __attribute__((deprecated))
#endif

#ifdef HERA_API_STATIC
	#define HERA_API_EXP
	#define HERA_API_NOEXP
	#define HERA_API_CC
#else
	#if defined _WIN32 || defined __CYGWIN__
		#ifdef HERA_API_EXPORTS
			#ifdef __GNUC__
				#define HERA_API_EXP __attribute__ ((dllexport))
			#else
				#define HERA_API_EXP __declspec(dllexport)
			#endif
		#else
			#ifdef __GNUC__
				#define HERA_API_EXP __attribute__ ((dllimport))
			#else
				#define HERA_API_EXP __declspec(dllimport)
			#endif
		#endif
		#define HERA_API_NOEXP
		#define HERA_API_CC __cdecl
	#else
		#if __GNUC__ >= 4
			#define HERA_API_EXP __attribute__ ((visibility ("default")))
			#define HERA_API_NOEXP  __attribute__ ((visibility ("hidden")))
		#else
			#define HERA_API_EXP
			#define HERA_API_NOEXP
		#endif
		#define HERA_API_CC
	#endif
#endif