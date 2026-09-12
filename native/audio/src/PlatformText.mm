// SPDX-License-Identifier: GPL-3.0-only
// Pure Foundation transform, kept outside the realtime audio implementation.
#import <Foundation/Foundation.h>
#include <cstddef>
#include <cstring>

extern "C" __attribute__((visibility("default"))) int32_t vm_platform_transliterate(
    const char *input, char *output, size_t capacity
) noexcept {
    if (input == nullptr || output == nullptr || capacity == 0) return -1;
    @try {
        @autoreleasepool {
            NSString *source = [NSString stringWithUTF8String:input];
            NSString *converted = [source stringByApplyingTransform:@"Any-Latin; Latin-ASCII" reverse:NO];
            if (converted == nil) return -1;
            const char *text = converted.UTF8String;
            const size_t size = std::strlen(text);
            if (size >= capacity) return -1;
            std::memcpy(output, text, size + 1);
            return 0;
        }
    } @catch (NSException *exception) {
        (void)exception;
        return -1;
    }
}
