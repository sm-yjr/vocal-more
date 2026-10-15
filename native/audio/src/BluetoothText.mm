// SPDX-License-Identifier: GPL-3.0-only
#import <CoreBluetooth/CoreBluetooth.h>
#import <Foundation/Foundation.h>
#include "vocal_more_bluetooth.h"
#include <deque>
#include <vector>

// V2 peers use a distinct service: V1 must never type the new envelope literally.
static CBUUID *serviceUUID() { return [CBUUID UUIDWithString:@"76B6AC72-0244-45B3-8F19-C06B6A0F4450"]; }
static CBUUID *textUUID() { return [CBUUID UUIDWithString:@"76B6AC72-0244-45B3-8F19-C06B6A0F4451"]; }
static CBUUID *receiptUUID() { return [CBUUID UUIDWithString:@"76B6AC72-0244-45B3-8F19-C06B6A0F4452"]; }
struct BTEvent { uint32_t kind, code; std::vector<uint8_t> data; };
@interface VMBluetoothText : NSObject<CBCentralManagerDelegate, CBPeripheralDelegate, CBPeripheralManagerDelegate> {
@public
    std::deque<BTEvent> events;
}
@property(nonatomic) uint32_t role;
@property(nonatomic) uint32_t state;
@property(nonatomic) BOOL closed;
@property(nonatomic) BOOL writePending;
@property(nonatomic) BOOL approved;
@property(nonatomic, copy) NSString *peer;
@property(nonatomic, strong) CBCentralManager *central;
@property(nonatomic, strong) CBPeripheralManager *receiver;
@property(nonatomic, strong) NSMutableDictionary<NSString *, CBPeripheral *> *discovered;
@property(nonatomic, strong) CBPeripheral *peripheral;
@property(nonatomic, strong) CBCharacteristic *text;
@property(nonatomic, strong) CBCharacteristic *receipt;
@property(nonatomic, strong) CBMutableCharacteristic *publishedReceipt;
@property(nonatomic, strong) CBMutableCharacteristic *publishedText;
@property(nonatomic, strong) NSMutableSet<CBUUID *> *subscriptions;
@property(nonatomic, strong) CBCentral *subscriber;
@property(nonatomic, strong) NSMutableArray<NSDictionary *> *notifications;
@property(nonatomic, copy) NSString *pairingCode;
- (void)push:(uint32_t)kind code:(uint32_t)code data:(NSData *)data;
- (void)status:(uint32_t)state;
- (void)choose:(NSString *)peer;
- (void)close;
- (void)drain;
- (void)ready;
@end
@implementation VMBluetoothText
- (void)push:(uint32_t)kind code:(uint32_t)code data:(NSData *)data {
    if (self.closed || data.length > 512) return;
    if (events.size() >= 64) { [self close]; return; }
    BTEvent event{kind, code, {}};
    if (data.length) { const auto *p = static_cast<const uint8_t *>(data.bytes); event.data.assign(p, p + data.length); }
    events.push_back(std::move(event));
}
- (void)status:(uint32_t)state {
    if (self.state != state) { self.state = state; [self push:1 code:state data:nil]; }
}
- (void)ready {
    if (self.approved && self.text.isNotifying && self.receipt.isNotifying) [self status:5];
}
- (void)choose:(NSString *)peer {
    self.peer = peer ?: @"";
    if (self.role == 1 && self.central.state == CBManagerStatePoweredOn) {
        [self.central stopScan];
        if (self.peripheral) [self.central cancelPeripheralConnection:self.peripheral];
        self.peripheral.delegate = nil; self.peripheral = nil; self.text = nil; self.receipt = nil;
        self.writePending = NO; self.approved = NO;
        CBPeripheral *candidate = self.discovered[self.peer];
        if (!candidate && self.peer.length) {
            NSUUID *identifier = [[NSUUID alloc] initWithUUIDString:self.peer];
            if (identifier) candidate = [self.central retrievePeripheralsWithIdentifiers:@[identifier]].firstObject;
        }
        if (candidate) {
            self.peripheral = candidate; candidate.delegate = self;
            [self status:3]; [self.central connectPeripheral:candidate options:nil];
        } else {
            [self status:2]; [self.central scanForPeripheralsWithServices:@[serviceUUID()] options:nil];
        }
    } else if (self.role == 2 && self.subscriber && [self.subscriptions containsObject:receiptUUID()]) {
        [self.notifications removeAllObjects];
        uint8_t accepted = [self.subscriber.identifier.UUIDString isEqualToString:self.peer] ? 0xA1 : 0xA0;
        NSMutableData *hello = [NSMutableData dataWithBytes:&accepted length:1];
        if (accepted == 0xA0) [hello appendData:[self.pairingCode dataUsingEncoding:NSUTF8StringEncoding]];
        [self.notifications addObject:@{@"data":hello, @"text":@NO}];
        [self drain]; [self status:accepted == 0xA1 && [self.subscriptions containsObject:textUUID()] ? 5 : 4];
    }
}
- (void)centralManagerDidUpdateState:(CBCentralManager *)central {
    if (self.closed) return;
    if (central.state == CBManagerStatePoweredOn) [self choose:self.peer];
    else [self status:central.state == CBManagerStateUnauthorized ? 7 : (central.state == CBManagerStateUnsupported ? 8 : 6)];
}
- (void)centralManager:(CBCentralManager *)central didDiscoverPeripheral:(CBPeripheral *)peripheral
     advertisementData:(NSDictionary<NSString *, id> *)advertisementData RSSI:(NSNumber *)RSSI {
    (void)advertisementData; (void)RSSI;
    if (self.closed || self.discovered.count >= 32 || self.discovered[peripheral.identifier.UUIDString]) return;
    NSString *identifier = peripheral.identifier.UUIDString;
    self.discovered[identifier] = peripheral;
    [self push:8 code:0 data:[identifier dataUsingEncoding:NSUTF8StringEncoding]];
    if ([identifier isEqualToString:self.peer]) { [central stopScan]; [self choose:self.peer]; }
}
- (void)centralManager:(CBCentralManager *)central didConnectPeripheral:(CBPeripheral *)peripheral {
    (void)central;
    if (!self.closed && peripheral == self.peripheral) [peripheral discoverServices:@[serviceUUID()]];
}
- (void)centralManager:(CBCentralManager *)central didFailToConnectPeripheral:(CBPeripheral *)peripheral error:(NSError *)error {
    (void)central; (void)error;
    if (peripheral == self.peripheral) [self status:6];
}
- (void)centralManager:(CBCentralManager *)central didDisconnectPeripheral:(CBPeripheral *)peripheral error:(NSError *)error {
    (void)central; (void)error;
    if (peripheral == self.peripheral) { self.writePending = NO; self.approved = NO; self.text = nil; self.receipt = nil; [self status:6]; }
}
- (void)peripheral:(CBPeripheral *)peripheral didDiscoverServices:(NSError *)error {
    if (self.closed || peripheral != self.peripheral) return;
    if (error) { [self status:9]; return; }
    BOOL found = NO;
    for (CBService *service in peripheral.services) {
        if ([service.UUID isEqual:serviceUUID()]) { found = YES; [peripheral discoverCharacteristics:@[textUUID(), receiptUUID()] forService:service]; }
    }
    if (!found) [self status:9];
}
- (void)peripheral:(CBPeripheral *)peripheral didModifyServices:(NSArray<CBService *> *)invalidatedServices {
    if (self.closed || peripheral != self.peripheral) return;
    for (CBService *service in invalidatedServices) {
        if ([service.UUID isEqual:serviceUUID()]) {
            // The peer app can exit while macOS retains the physical link.
            // Its old GATT objects are no longer a usable application session.
            self.writePending = NO; self.approved = NO; self.text = nil; self.receipt = nil;
            [self status:6]; [self.central cancelPeripheralConnection:peripheral]; return;
        }
    }
}
- (void)peripheral:(CBPeripheral *)peripheral didDiscoverCharacteristicsForService:(CBService *)service error:(NSError *)error {
    if (self.closed || peripheral != self.peripheral) return;
    if (error) { [self status:9]; return; }
    for (CBCharacteristic *characteristic in service.characteristics) {
        if ([characteristic.UUID isEqual:textUUID()]) self.text = characteristic;
        if ([characteristic.UUID isEqual:receiptUUID()]) self.receipt = characteristic;
    }
    if (self.text && self.receipt) {
        [self status:4];
        [peripheral setNotifyValue:YES forCharacteristic:self.text];
        [peripheral setNotifyValue:YES forCharacteristic:self.receipt];
    } else [self status:9];
}
- (void)peripheral:(CBPeripheral *)peripheral didUpdateNotificationStateForCharacteristic:(CBCharacteristic *)characteristic error:(NSError *)error {
    if (peripheral != self.peripheral || self.closed) return;
    if (error || !characteristic.isNotifying) [self status:9]; else [self ready];
}
- (void)peripheral:(CBPeripheral *)peripheral didUpdateValueForCharacteristic:(CBCharacteristic *)characteristic error:(NSError *)error {
    if (self.closed || peripheral != self.peripheral) return;
    if (error) { [self status:9]; return; }
    NSData *value = characteristic.value;
    if (characteristic == self.text) {
        if (self.state == 5) [self push:3 code:0 data:value];
        return;
    }
    if (characteristic != self.receipt) return;
    if (value.length == 7 && *static_cast<const uint8_t *>(value.bytes) == 0xA0) {
        [self push:10 code:0 data:[value subdataWithRange:NSMakeRange(1, 6)]];
        [self status:4]; return;
    }
    if (value.length == 1) {
        uint8_t code = *static_cast<const uint8_t *>(value.bytes);
        self.approved = code == 0xA1;
        if (self.approved) [self ready]; else [self status:4];
    } else if (self.state == 5) {
        uint32_t kind = value.length && *static_cast<const uint8_t *>(value.bytes) == 0xB2 ? 12 : 4;
        [self push:kind code:0 data:value];
    }
}
- (void)peripheral:(CBPeripheral *)peripheral didWriteValueForCharacteristic:(CBCharacteristic *)characteristic error:(NSError *)error {
    if (self.closed || peripheral != self.peripheral) return;
    if (characteristic == self.receipt) { if (error) [self push:13 code:1 data:nil]; return; }
    if (characteristic != self.text) return;
    self.writePending = NO;
    [self push:2 code:error ? 1 : 0 data:nil];
}
- (void)peripheralManagerDidUpdateState:(CBPeripheralManager *)manager {
    if (self.closed) return;
    if (manager.state != CBManagerStatePoweredOn) { [self status:manager.state == CBManagerStateUnauthorized ? 7 : (manager.state == CBManagerStateUnsupported ? 8 : 6)]; return; }
    CBCharacteristicProperties properties = CBCharacteristicPropertyWrite | CBCharacteristicPropertyNotifyEncryptionRequired;
    CBAttributePermissions permissions = CBAttributePermissionsReadEncryptionRequired | CBAttributePermissionsWriteEncryptionRequired;
    self.publishedText = [[CBMutableCharacteristic alloc] initWithType:textUUID()
        properties:properties value:nil permissions:permissions];
    self.publishedReceipt = [[CBMutableCharacteristic alloc] initWithType:receiptUUID()
        properties:properties value:nil permissions:permissions];
    CBMutableService *service = [[CBMutableService alloc] initWithType:serviceUUID() primary:YES];
    service.characteristics = @[self.publishedText, self.publishedReceipt];
    [manager addService:service];
}
- (void)peripheralManager:(CBPeripheralManager *)manager didAddService:(CBService *)service error:(NSError *)error {
    (void)service;
    if (self.closed) return;
    if (error) { [self status:9]; return; }
    [manager startAdvertising:@{CBAdvertisementDataServiceUUIDsKey:@[serviceUUID()], CBAdvertisementDataLocalNameKey:@"Vocal More"}];
}
- (void)peripheralManagerDidStartAdvertising:(CBPeripheralManager *)manager error:(NSError *)error {
    (void)manager; [self status:error ? 9 : 2];
}
- (void)peripheralManager:(CBPeripheralManager *)manager central:(CBCentral *)central didSubscribeToCharacteristic:(CBCharacteristic *)characteristic {
    (void)manager;
    if (self.closed || (characteristic != self.publishedReceipt && characteristic != self.publishedText)) return;
    if (self.subscriber && ![central.identifier isEqual:self.subscriber.identifier]) return;
    BOOL first = self.subscriber == nil;
    self.subscriber = central;
    [self.subscriptions addObject:characteristic.UUID];
    if (first && ![central.identifier.UUIDString isEqualToString:self.peer]) {
        self.pairingCode = [NSString stringWithFormat:@"%06u", arc4random_uniform(1000000)];
        [self push:9 code:0 data:[central.identifier.UUIDString dataUsingEncoding:NSUTF8StringEncoding]];
        [self push:10 code:0 data:[self.pairingCode dataUsingEncoding:NSUTF8StringEncoding]];
    }
    [self choose:self.peer];
}
- (void)peripheralManager:(CBPeripheralManager *)manager central:(CBCentral *)central didUnsubscribeFromCharacteristic:(CBCharacteristic *)characteristic {
    (void)manager;
    if (![central.identifier isEqual:self.subscriber.identifier]) return;
    [self.subscriptions removeObject:characteristic.UUID];
    if (!self.subscriptions.count) self.subscriber = nil;
    self.writePending = NO; [self.notifications removeAllObjects]; [self status:2];
}
- (void)peripheralManager:(CBPeripheralManager *)manager didReceiveWriteRequests:(NSArray<CBATTRequest *> *)requests {
    if (self.closed) return;
    for (CBATTRequest *request in requests) {
        if (self.state != 5 || ![request.central.identifier.UUIDString isEqualToString:self.peer]) {
            [manager respondToRequest:requests.firstObject withResult:CBATTErrorInsufficientAuthorization]; return;
        }
        BOOL text = [request.characteristic.UUID isEqual:textUUID()];
        BOOL receipt = [request.characteristic.UUID isEqual:receiptUUID()];
        if ((!text && !receipt) || request.offset || !request.value.length || request.value.length > (text ? 512 : 20) || events.size() + requests.count > 64) {
            [manager respondToRequest:requests.firstObject withResult:CBATTErrorInvalidAttributeValueLength]; return;
        }
    }
    for (CBATTRequest *request in requests) {
        uint32_t kind = [request.characteristic.UUID isEqual:textUUID()] ? 3 :
            (*static_cast<const uint8_t *>(request.value.bytes) == 0xB2 ? 12 : 4);
        [self push:kind code:0 data:request.value];
    }
    if (requests.count) [manager respondToRequest:requests.firstObject withResult:CBATTErrorSuccess];
}
- (void)drain {
    while (!self.closed && self.subscriber && self.notifications.count) {
        NSDictionary *item = self.notifications.firstObject;
        BOOL text = [item[@"text"] boolValue];
        CBMutableCharacteristic *characteristic = text ? self.publishedText : self.publishedReceipt;
        if (![self.subscriptions containsObject:characteristic.UUID]) break;
        if (![self.receiver updateValue:item[@"data"] forCharacteristic:characteristic onSubscribedCentrals:@[self.subscriber]]) break;
        [self.notifications removeObjectAtIndex:0];
        // Queue admission only; the end-to-end receipt confirms insertion.
        if (text) { self.writePending = NO; [self push:2 code:0 data:nil]; }
    }
}
- (void)peripheralManagerIsReadyToUpdateSubscribers:(CBPeripheralManager *)manager { (void)manager; [self drain]; }
- (void)close {
    if (self.closed) return;
    self.closed = YES; self.state = 0;
    self.central.delegate = nil; self.receiver.delegate = nil; self.peripheral.delegate = nil;
    [self.central stopScan];
    if (self.peripheral) [self.central cancelPeripheralConnection:self.peripheral];
    [self.receiver stopAdvertising]; [self.receiver removeAllServices];
    [self.notifications removeAllObjects]; [self.discovered removeAllObjects]; [self.subscriptions removeAllObjects]; events.clear();
    self.subscriber = nil; self.peripheral = nil; self.central = nil; self.receiver = nil;
}
@end
static VMBluetoothText *get(void *handle) { return (__bridge VMBluetoothText *)handle; }
uint32_t vm_bt_abi_version(void) { return 2; }
uint32_t vm_bt_authorization(void) { return static_cast<uint32_t>(CBManager.authorization); }
void *vm_bt_create(uint32_t role, const char *peer, int request_permission) {
    if (![NSThread isMainThread] || (role != 1 && role != 2)) return nullptr;
    VMBluetoothText *session = [VMBluetoothText new];
    session.role = role; session.peer = peer ? [NSString stringWithUTF8String:peer] : @"";
    session.discovered = [NSMutableDictionary new]; session.notifications = [NSMutableArray new];
    session.subscriptions = [NSMutableSet new];
    if (CBManager.authorization != CBManagerAuthorizationAllowedAlways && !request_permission) [session status:7];
    else if (role == 1) session.central = [[CBCentralManager alloc] initWithDelegate:session queue:dispatch_get_main_queue()];
    else session.receiver = [[CBPeripheralManager alloc] initWithDelegate:session queue:dispatch_get_main_queue()];
    return (__bridge_retained void *)session;
}
void vm_bt_close(void *handle) {
    if (!handle || ![NSThread isMainThread]) return;
    VMBluetoothText *session = (__bridge_transfer VMBluetoothText *)handle; [session close];
}
void vm_bt_set_peer(void *handle, const char *peer) {
    if (handle && [NSThread isMainThread]) {
        // Only explicit selection changes ownership. Internal subscription
        // readiness must preserve the freshly generated approval/code events.
        VMBluetoothText *session=get(handle);
        session->events.clear();
        [session choose:peer ? [NSString stringWithUTF8String:peer] : @""];
    }
}
uint32_t vm_bt_mtu(void *handle) {
    if (!handle || ![NSThread isMainThread]) return 20;
    VMBluetoothText *session = get(handle);
    if (session.role == 2) return static_cast<uint32_t>(MIN(512, session.subscriber ? session.subscriber.maximumUpdateValueLength : 20));
    if (!session.peripheral) return 20;
    // Bound individual frames to the negotiated short-write payload, even
    // though delivery itself uses write-with-response. Avoid ATT long writes.
    NSUInteger shortWrite = [session.peripheral maximumWriteValueLengthForType:CBCharacteristicWriteWithoutResponse];
    NSUInteger acknowledgedWrite = [session.peripheral maximumWriteValueLengthForType:CBCharacteristicWriteWithResponse];
    return static_cast<uint32_t>(MIN(512, MIN(shortWrite, acknowledgedWrite)));
}
int vm_bt_send(void *handle, const uint8_t *bytes, uint32_t length) {
    if (!handle || !bytes || !length || ![NSThread isMainThread]) return 0;
    VMBluetoothText *session = get(handle);
    if (session.closed || session.state != 5 || session.writePending || length > vm_bt_mtu(handle)) return 0;
    NSData *data = [NSData dataWithBytes:bytes length:length];
    if (session.role == 1 && session.text) {
        session.writePending = YES; [session.peripheral writeValue:data forCharacteristic:session.text type:CBCharacteristicWriteWithResponse]; return 1;
    }
    if (session.role == 2 && session.subscriber && [session.subscriptions containsObject:textUUID()] && session.notifications.count < 16) {
        session.writePending = YES; [session.notifications addObject:@{@"data":data, @"text":@YES}]; [session drain]; return 1;
    }
    return 0;
}
int vm_bt_notify(void *handle, const uint8_t *bytes, uint32_t length) {
    if (!handle || !bytes || !length || length > 20 || ![NSThread isMainThread]) return 0;
    VMBluetoothText *session = get(handle);
    if (session.closed || session.state != 5) return 0;
    NSData *data = [NSData dataWithBytes:bytes length:length];
    if (session.role == 1 && session.receipt) {
        [session.peripheral writeValue:data forCharacteristic:session.receipt type:CBCharacteristicWriteWithResponse]; return 1;
    }
    if (session.role == 2 && session.subscriber && session.notifications.count < 16) {
        [session.notifications addObject:@{@"data":data, @"text":@NO}]; [session drain]; return 1;
    }
    return 0;
}
int vm_bt_poll(void *handle, uint32_t *kind, uint32_t *code, uint8_t *bytes, uint32_t capacity, uint32_t *length) {
    if (!handle || !kind || !code || !bytes || !length || ![NSThread isMainThread]) return 0;
    VMBluetoothText *session = get(handle);
    if (session.closed) { *kind = 1; *code = 9; *length = 0; return -1; }
    if (session->events.empty()) return 0;
    const auto &event = session->events.front();
    if (event.data.size() > capacity) return -1;
    *kind = event.kind; *code = event.code; *length = static_cast<uint32_t>(event.data.size());
    if (*length) memcpy(bytes, event.data.data(), *length);
    session->events.pop_front(); return 1;
}
