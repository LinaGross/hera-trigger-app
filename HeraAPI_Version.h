#pragma once

#ifndef HERA_API_VER_MAJOR
	#define HERA_API_VER_MAJOR 0
#endif
#ifndef HERA_API_VER_MINOR
	#define HERA_API_VER_MINOR 0
#endif
#ifndef HERA_API_VER_BUILD
	#define HERA_API_VER_BUILD 0
#endif

#ifndef HERA_API_VERSION
	#define HERA_API_VERSION    (((HERA_API_VER_MAJOR)<<24) + ((HERA_API_VER_MINOR)<<16) + (HERA_API_VER_BUILD))
#endif