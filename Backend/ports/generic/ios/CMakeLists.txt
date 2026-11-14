enable_language(OBJC OBJCXX)
set(CMAKE_OBJCXX_STANDARD 20)
set(CMAKE_OBJCXX_STANDARD_REQUIRED ON)

if(NOT PS2_AOT_ONLY OR NOT TARGET ps2game OR NOT EXISTS "${PS2_WGPU_DIR}/lib/libwgpu_native.a")
    message(FATAL_ERROR "PS2Apple needs generated AOT code and the iPhoneOS Metal shader runtime.")
endif()

set(PS2APPLE_LAUNCHER main.mm "${CMAKE_SOURCE_DIR}/host/stretch.cpp"
    "${PS2APPLE_SHARED_IOS}/PS2Launcher.m" "${PS2APPLE_SHARED_IOS}/PS2DiscPicker.m"
    "${PS2APPLE_SHARED_IOS}/PS2Touch.m")
add_executable(PS2Game MACOSX_BUNDLE ${PS2APPLE_LAUNCHER})
set_target_properties(PS2Game PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}")
target_include_directories(PS2Game PRIVATE "${PS2APPLE_SDL_INCLUDE}" "${PS2APPLE_SHARED_IOS}" "${CMAKE_SOURCE_DIR}/host")
target_compile_options(PS2Game PRIVATE -fobjc-arc -fblocks)
target_link_libraries(PS2Game PRIVATE ps2game ps2runtime "${PS2APPLE_SDL}" ${PS2APPLE_SDL_FLAGS}
    "-framework UIKit" "-framework Foundation" "-framework QuartzCore" "-framework Metal"
    "-framework UniformTypeIdentifiers" objc)
target_link_options(PS2Game PRIVATE -Wl,-ObjC -Wl,-dead_strip)

if(PS2APPLE_WINDOWS_KIT)
    add_library(ps2apple_ios_launcher STATIC ${PS2APPLE_LAUNCHER})
    target_include_directories(ps2apple_ios_launcher PRIVATE "${PS2APPLE_SDL_INCLUDE}" "${PS2APPLE_SHARED_IOS}" "${CMAKE_SOURCE_DIR}/host")
    target_compile_options(ps2apple_ios_launcher PRIVATE -fobjc-arc -fblocks)
    target_link_libraries(ps2apple_ios_launcher PRIVATE ps2runtime)
endif()
