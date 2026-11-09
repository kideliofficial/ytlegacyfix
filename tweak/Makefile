# Default: 32-bit + 64-bit (iPhone 4s/5/5c and newer). Needs iPhoneOS9.3.sdk in $THEOS/sdks
#   and may need an older Xcode.
# 64-bit devices only (iPhone 5s+, iPad Air/mini 2+), uses the SDK Theos already has:
#   make package ARM64ONLY=1
ifeq ($(ARM64ONLY),1)
TARGET := iphone:clang:latest:7.0
ARCHS = arm64
else
TARGET := iphone:clang:9.3:7.0
ARCHS = armv7 arm64
endif

INSTALL_TARGET_PROCESSES = YouTube

include $(THEOS)/makefiles/common.mk

TWEAK_NAME = YTLegacyFix
YTLegacyFix_FILES = Tweak.x signin.x general.m TRJSONUtils.m
YTLegacyFix_CFLAGS = -fno-objc-arc
PROXY_SCHEME ?= https
PROXY_HOST ?= proxy.example.invalid
PROXY_PORT ?= 443
YTLegacyFix_CFLAGS += -DYT_PROXY_SCHEME='@"$(PROXY_SCHEME)"'
YTLegacyFix_CFLAGS += -DYT_PROXY_HOST='@"$(PROXY_HOST)"'
YTLegacyFix_CFLAGS += -DYT_PROXY_PORT=$(PROXY_PORT)
YTLegacyFix_FRAMEWORKS = UIKit AVFoundation MediaPlayer
YTLegacyFix_USE_MODULES = 0
YTLegacyFix_RESOURCE_DIRS = layout
YTLegacyFix_LDFLAGS = -Wl,-ld_classic -Wl,-undefined,dynamic_lookup

include $(THEOS_MAKE_PATH)/tweak.mk
