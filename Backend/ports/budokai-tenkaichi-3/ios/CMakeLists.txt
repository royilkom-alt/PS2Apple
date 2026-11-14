enable_language(OBJC OBJCXX)
set(CMAKE_OBJCXX_STANDARD 20)
set(CMAKE_OBJCXX_STANDARD_REQUIRED ON)

if(NOT PS2_AOT_ONLY OR NOT TARGET ps2game OR NOT EXISTS "${PS2_WGPU_DIR}/lib/libwgpu_native.a")
    message(FATAL_ERROR "PS2Apple needs generated AOT code and the iPhoneOS Metal shader runtime.")
endif()

add_executable(PS2Game MACOSX_BUNDLE main.mm
    "${PS2APPLE_SHARED_IOS}/PS2Launcher.m" "${PS2APPLE_SHARED_IOS}/PS2DiscPicker.m"
    "${PS2APPLE_SHARED_IOS}/PS2Touch.m")
set_target_properties(PS2Game PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}")
target_include_directories(PS2Game PRIVATE "${PS2APPLE_SDL_INCLUDE}" "${PS2APPLE_SHARED_IOS}" "${CMAKE_SOURCE_DIR}/host")
target_compile_options(PS2Game PRIVATE -fobjc-arc -fblocks)
target_link_libraries(PS2Game PRIVATE ps2game ps2runtime "${PS2APPLE_SDL}" ${PS2APPLE_SDL_FLAGS}
    "-framework UIKit" "-framework Foundation" "-framework QuartzCore" "-framework Metal"
    "-framework UniformTypeIdentifiers" objc)
target_link_options(PS2Game PRIVATE -Wl,-ObjC -Wl,-dead_strip)
