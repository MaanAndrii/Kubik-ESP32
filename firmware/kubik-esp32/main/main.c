#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/event_groups.h"
#include "esp_event.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_http_client.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "nvs_flash.h"
#include "cJSON.h"
#include "lvgl.h"
#include "bsp/esp-bsp.h"
#include "bsp/display.h"

typedef struct {
    char emotion[32], mode[24], location[24], held[32], wear[128];
    bool sleeping;
    float visibility;
    int64_t received;
} State;
static State state;
static SemaphoreHandle_t state_lock;
static QueueHandle_t touches;
static lv_obj_t *eye_left, *eye_right, *mouth, *status_label, *item_label, *wear_label;
static uint32_t session_id, sequence;
static int64_t last_touch;
static EventGroupHandle_t wifi_events;
#define CONNECTED BIT0

static void wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) xEventGroupSetBits(wifi_events, CONNECTED);
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(wifi_events, CONNECTED);
        esp_wifi_connect();
    }
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) esp_wifi_connect();
}

static void touch_event(lv_event_t *event)
{
    (void)event;
    int64_t now = esp_timer_get_time();
    if (now - last_touch < 300000) return;
    last_touch = now;
    uint32_t id = ++sequence;
    xQueueSend(touches, &id, 0);
}

/* Called exclusively by the network task; bounded response avoids heap growth. */
static int request(const char *path, const char *body, char *response, size_t capacity)
{
    char url[256], auth[160];
    snprintf(url, sizeof(url), "%s%s", CONFIG_KUBIK_DESKONE_URL, path);
    snprintf(auth, sizeof(auth), "Bearer %s", CONFIG_KUBIK_TOKEN);
    esp_http_client_config_t config = {.url=url, .timeout_ms=1500};
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) return -1;
    esp_http_client_set_header(client, "Authorization", auth);
    if (body) {
        esp_http_client_set_method(client, HTTP_METHOD_POST);
        esp_http_client_set_header(client, "Content-Type", "application/json");
    }
    int status = -1;
    int length = body ? strlen(body) : 0;
    if (esp_http_client_open(client, length) == ESP_OK) {
        if (!body || esp_http_client_write(client, body, length) == length) {
            int64_t declared = esp_http_client_fetch_headers(client);
            status = esp_http_client_get_status_code(client);
            if (response) {
                if (declared < 0 || declared >= (int64_t)capacity) status = -1;
                else {
                    int used = 0, got;
                    while (used < (int)capacity - 1 &&
                           (got = esp_http_client_read(client, response + used,
                                                     capacity - 1 - used)) > 0) used += got;
                    response[used] = 0;
                    if (!esp_http_client_is_complete_data_received(client)) status = -1;
                }
            }
        }
    }
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    return status;
}

static bool copy_string(cJSON *root, const char *name, char *out, size_t capacity)
{
    cJSON *v = cJSON_GetObjectItemCaseSensitive(root, name);
    if (!cJSON_IsString(v) || strlen(v->valuestring) >= capacity) return false;
    snprintf(out, capacity, "%s", v->valuestring);
    return true;
}

static void accept_state(const char *payload)
{
    cJSON *root = cJSON_Parse(payload);
    if (!root) return;
    State next = {0};
    cJSON *version = cJSON_GetObjectItemCaseSensitive(root, "version");
    cJSON *ready = cJSON_GetObjectItemCaseSensitive(root, "ready");
    cJSON *sleep = cJSON_GetObjectItemCaseSensitive(root, "sleeping");
    cJSON *visibility = cJSON_GetObjectItemCaseSensitive(root, "visibility");
    cJSON *wear = cJSON_GetObjectItemCaseSensitive(root, "wearables");
    bool valid = cJSON_IsNumber(version) && version->valuedouble == 1 &&
        cJSON_IsTrue(ready) && cJSON_IsBool(sleep) && cJSON_IsNumber(visibility) &&
        cJSON_IsArray(wear) && cJSON_GetArraySize(wear) <= 2 &&
        copy_string(root, "emotion", next.emotion, sizeof(next.emotion)) &&
        copy_string(root, "mode", next.mode, sizeof(next.mode)) &&
        copy_string(root, "location", next.location, sizeof(next.location)) &&
        copy_string(root, "held", next.held, sizeof(next.held));
    if (valid) {
        cJSON *item;
        cJSON_ArrayForEach(item, wear) {
            if (!cJSON_IsString(item) || strlen(item->valuestring) > 48) {valid=false; break;}
            if (next.wear[0]) strcat(next.wear, " / ");
            strcat(next.wear, item->valuestring);
        }
    }
    if (valid) {
        next.sleeping = cJSON_IsTrue(sleep);
        next.visibility = visibility->valuedouble < 0 ? 0 :
            visibility->valuedouble > 1 ? 1 : visibility->valuedouble;
        next.received = esp_timer_get_time();
        xSemaphoreTake(state_lock, portMAX_DELAY);
        state = next;
        xSemaphoreGive(state_lock);
    }
    cJSON_Delete(root);
}

static void network_task(void *arg)
{
    (void)arg;
    char response[2048], body[128];
    for (;;) {
        if (xEventGroupGetBits(wifi_events) & CONNECTED) {
            uint32_t id;
            /* Touch is best effort: never replay an old gesture after reconnect. */
            if (xQueueReceive(touches, &id, 0) == pdTRUE) {
                snprintf(body, sizeof(body),
                    "{\"id\":\"%08lx-%08lx\",\"action\":\"touch\"}",
                    (unsigned long)session_id, (unsigned long)id);
                request("/v1/touch", body, NULL, 0);
            }
            if (request("/v1/state", NULL, response, sizeof(response)) == 200)
                accept_state(response);
        } else {
            xQueueReset(touches);
        }
        vTaskDelay(pdMS_TO_TICKS(500));
    }
}

static void refresh(lv_timer_t *timer)
{
    (void)timer;
    State current;
    xSemaphoreTake(state_lock, portMAX_DELAY);
    current = state;
    xSemaphoreGive(state_lock);
    int64_t now = esp_timer_get_time();
    bool online = current.received && now - current.received < 5000000;
    bool blink = (now / 1000 % 4000) < 130;
    bool asleep = !online || current.sleeping;
    lv_obj_set_height(eye_left, asleep || blink ? 8 : 56);
    lv_obj_set_height(eye_right, asleep || blink ? 8 : 56);
    lv_label_set_text(mouth, asleep ? "-" :
        strcmp(current.emotion, "SAD") == 0 ? "~" : "o");
    if (online) {
        lv_label_set_text_fmt(status_label, "%s / %s\n%s",
            current.location, current.mode, current.emotion);
        lv_label_set_text(item_label, current.held);
        lv_obj_set_style_opa(item_label, (lv_opa_t)(255 * current.visibility), 0);
        lv_label_set_text(wear_label, current.wear);
    } else {
        lv_label_set_text(status_label, "KUBIK\nOffline - waiting for DeskOne");
        lv_label_set_text(item_label, "");
        lv_label_set_text(wear_label, "");
    }
}

static lv_obj_t *label_at(lv_obj_t *parent, int x, int y)
{
    lv_obj_t *obj = lv_label_create(parent);
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_width(obj, 420);
    lv_obj_set_style_text_align(obj, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_color(obj, lv_color_white(), 0);
    return obj;
}

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    state_lock = xSemaphoreCreateMutex();
    touches = xQueueCreate(4, sizeof(uint32_t));
    configASSERT(state_lock && touches);
    wifi_events = xEventGroupCreate();
    configASSERT(wifi_events);
    session_id = esp_random();
    bsp_display_start();
    bsp_display_lock(-1);
    lv_obj_t *screen = lv_screen_active();
    lv_obj_set_style_bg_color(screen, lv_color_black(), 0);
    lv_obj_add_flag(screen, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(screen, touch_event, LV_EVENT_CLICKED, NULL);
    lv_obj_t *body = lv_obj_create(screen);
    lv_obj_set_pos(body, 70, 100);
    lv_obj_set_size(body, 340, 270);
    lv_obj_remove_flag(body, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(body, LV_OBJ_FLAG_EVENT_BUBBLE);
    lv_obj_set_style_bg_color(body, lv_color_hex(0x344d72), 0);
    lv_obj_set_style_radius(body, 48, 0);
    eye_left = lv_obj_create(body);
    eye_right = lv_obj_create(body);
    lv_obj_t *eyes[] = {eye_left, eye_right};
    for (int i=0; i<2; ++i) {
        lv_obj_set_pos(eyes[i], 65+i*140, 70);
        lv_obj_set_size(eyes[i], 35, 56);
        lv_obj_set_style_radius(eyes[i], LV_RADIUS_CIRCLE, 0);
        lv_obj_set_style_bg_color(eyes[i], lv_color_white(), 0);
        lv_obj_remove_flag(eyes[i], LV_OBJ_FLAG_CLICKABLE | LV_OBJ_FLAG_SCROLLABLE);
    }
    mouth = label_at(body, 0, 170);
    lv_obj_set_width(mouth, 310);
    status_label = label_at(screen, 30, 25);
    item_label = label_at(screen, 30, 385);
    wear_label = label_at(screen, 30, 420);
    lv_timer_create(refresh, 50, NULL);
    bsp_display_unlock();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event, NULL));
    wifi_config_t wifi = {0};
    strlcpy((char *)wifi.sta.ssid, CONFIG_KUBIK_WIFI_SSID, sizeof(wifi.sta.ssid));
    strlcpy((char *)wifi.sta.password, CONFIG_KUBIK_WIFI_PASSWORD, sizeof(wifi.sta.password));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi));
    if (strlen(CONFIG_KUBIK_WIFI_SSID)) ESP_ERROR_CHECK(esp_wifi_start());
    configASSERT(xTaskCreate(network_task, "deskone", 8192, NULL, 4, NULL) == pdPASS);
}
