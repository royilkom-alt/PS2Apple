cmake_minimum_required(VERSION 3.21)
project(PS2AppleShaolinMonks LANGUAGES C CXX OBJC OBJCXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_OBJCXX_STANDARD 20)
set(CMAKE_OBJCXX_STANDARD_REQUIRED ON)

set(runtime "${PS2APPLE_SOURCE}/ps2xRuntime")
set(iop "${PS2APPLE_SOURCE}/ps2xIOP")
if(NOT EXISTS "${PS2APPLE_GENERATED}/register_functions.cpp")
    message(FATAL_ERROR "Generate the retail PS2 sources before building the iPhoneOS target.")
endif()
if(NOT EXISTS "${PS2APPLE_GRAPHICS_LIBRARY}" OR NOT EXISTS "${PS2APPLE_WGPU}/lib/libwgpu_native.a")
    message(FATAL_ERROR "The shared iPhoneOS Metal runtime was not built.")
endif()
file(GLOB_RECURSE runtime_sources CONFIGURE_DEPENDS "${runtime}/src/lib/*.cpp")
list(FILTER runtime_sources EXCLUDE REGEX "ps2_(android_runtime|vita_runtime|debug_panel)\\.cpp$")
file(GLOB game_sources CONFIGURE_DEPENDS "${PS2APPLE_GENERATED}/*.cpp")

add_library(ps2apple_runtime STATIC ${runtime_sources})
target_include_directories(ps2apple_runtime PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}" PUBLIC "${runtime}/include" "${runtime}/src/lib/Kernel"
    "${iop}/include" "${PS2APPLE_RAYLIB}/src" "${PS2APPLE_SSE2NEON}" "${PS2APPLE_FFMPEG}/include")
target_compile_definitions(ps2apple_runtime PUBLIC PS2APPLE_IOS=1 USE_SSE2NEON PS2X_IOP_ENABLE_PLUGINS=0
    PS2X_HAS_FFMPEG=1 PS2X_ENABLE_IOP_RPC_TRACE=0)
target_compile_options(ps2apple_runtime PRIVATE -O2)

# Keep Raylib's Core Audio implementation and replace its desktop window with the SDL Metal host.
set(raudio "${PS2APPLE_RAYLIB}/src/raudio.c")
set_source_files_properties("${raudio}" PROPERTIES LANGUAGE OBJC)
add_library(ps2apple_audio STATIC "${raudio}" "${PS2APPLE_RAYLIB}/src/utils.c")
target_compile_definitions(ps2apple_audio PRIVATE EXTERNAL_CONFIG_FLAGS SUPPORT_MODULE_RAUDIO
    SUPPORT_FILEFORMAT_WAV SUPPORT_TRACELOG SUPPORT_STANDARD_FILEIO)
target_compile_options(ps2apple_audio PRIVATE "$<$<COMPILE_LANGUAGE:OBJC>:-fblocks>")

set(shared_ui "${PS2APPLE_SHARED_IOS}/PS2Launcher.m" "${PS2APPLE_SHARED_IOS}/PS2DiscPicker.m"
    "${PS2APPLE_SHARED_IOS}/PS2Touch.m")
add_executable(ShaolinMonks MACOSX_BUNDLE main.mm sdl_host.cpp metal_gs.cpp disc_import.cpp ${shared_ui} ${game_sources})
target_include_directories(ShaolinMonks BEFORE PRIVATE "${PS2APPLE_GENERATED}" "${PS2APPLE_SDL_INCLUDE}" "${PS2APPLE_SHARED_IOS}"
    "${PS2APPLE_GRAPHICS_SOURCE}/runtime/include" "${PS2APPLE_GRAPHICS_SOURCE}/runtime/src")
target_compile_definitions(ShaolinMonks PRIVATE PS2APPLE_IOS=1 PS2APPLE_BOOT_HASH="${PS2APPLE_BOOT_HASH}")
target_compile_options(ShaolinMonks PRIVATE -O2 "$<$<COMPILE_LANGUAGE:OBJC,OBJCXX>:-fobjc-arc;-fblocks>")
set_target_properties(ShaolinMonks PROPERTIES UNITY_BUILD ON UNITY_BUILD_BATCH_SIZE 12)
set_source_files_properties(main.mm sdl_host.cpp metal_gs.cpp disc_import.cpp ${shared_ui} PROPERTIES SKIP_UNITY_BUILD_INCLUSION ON)

target_link_libraries(ShaolinMonks PRIVATE ps2apple_runtime ps2apple_audio "${PS2APPLE_IOP}"
    "${PS2APPLE_GRAPHICS_LIBRARY}" "${PS2APPLE_WGPU}/lib/libwgpu_native.a"
    "${PS2APPLE_FFMPEG}/lib/libavcodec.a" "${PS2APPLE_FFMPEG}/lib/libswscale.a"
    "${PS2APPLE_FFMPEG}/lib/libavutil.a" "${PS2APPLE_SDL}" ${PS2APPLE_SDL_FLAGS}
    "-framework UIKit" "-framework Foundation" "-framework QuartzCore" "-framework CoreGraphics"
    "-framework AVFoundation" "-framework AudioToolbox" "-framework CoreAudio"
    "-framework Metal" "-framework IOSurface" "-framework IOKit" "-framework CoreFoundation"
    "-framework UniformTypeIdentifiers" objc)
target_link_options(ShaolinMonks PRIVATE -Wl,-ObjC -Wl,-dead_strip)
