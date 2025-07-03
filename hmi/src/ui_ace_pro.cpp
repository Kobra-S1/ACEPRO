#include "ui_ace_pro.h"
#include "lvgl.h"

// Private variables
static lv_obj_t *main_screen;
static lv_obj_t *status_bar;
static lv_obj_t *ip_label;
static lv_obj_t *ace_pro_status_label;
static lv_obj_t *printer_status_label;
static lv_obj_t *settings_button;
static lv_obj_t *settings_button_label;

// Filament spool slots
static lv_obj_t *spool_slot_1;
static lv_obj_t *spool_slot_2;
static lv_obj_t *spool_slot_3;
static lv_obj_t *spool_slot_4;

// Function prototypes
static void create_status_bar(lv_obj_t *parent);
static void create_filament_slots(lv_obj_t *parent);
static void create_bottom_bar(lv_obj_t *parent);
static void settings_button_event_handler(lv_event_t *e);

void ui_ace_pro_init() {
    main_screen = lv_obj_create(NULL);
    lv_scr_load(main_screen);
    lv_obj_set_style_border_width(main_screen, 0, 0);
    lv_obj_set_style_pad_all(main_screen, 0, 0);

    create_status_bar(main_screen);
    create_filament_slots(main_screen);
    create_bottom_bar(main_screen);
}

void ui_ace_pro_update(const char* ip, const char* ace_pro_status, const char* printer_status) {
    lv_label_set_text(ip_label, ip);
    lv_label_set_text(ace_pro_status_label, ace_pro_status);
    lv_label_set_text(printer_status_label, printer_status);
}

static void create_status_bar(lv_obj_t *parent) {
    status_bar = lv_obj_create(parent);
    lv_obj_set_size(status_bar, LV_HOR_RES, 45);
    lv_obj_align(status_bar, LV_ALIGN_TOP_MID, 0, 0);
    lv_obj_set_style_bg_color(status_bar, lv_color_hex(0xADD8E6), 0); // Light Blue
    lv_obj_set_style_border_width(status_bar, 0, 0);
    lv_obj_set_style_pad_all(status_bar, 5, 0);
    lv_obj_set_flex_flow(status_bar, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(status_bar, LV_FLEX_ALIGN_SPACE_BETWEEN, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);

    ip_label = lv_label_create(status_bar);
    lv_label_set_text(ip_label, "IP: ---.---.---.---");
    lv_obj_set_style_text_font(ip_label, &lv_font_montserrat_18, 0);
    lv_obj_set_style_text_color(ip_label, lv_color_white(), 0);

    ace_pro_status_label = lv_label_create(status_bar);
    lv_label_set_text(ace_pro_status_label, "ACE: Unknown");
    lv_obj_set_style_text_font(ace_pro_status_label, &lv_font_montserrat_18, 0);
    lv_obj_set_style_text_color(ace_pro_status_label, lv_color_white(), 0);

    printer_status_label = lv_label_create(status_bar);
    lv_label_set_text(printer_status_label, "Printer: Unknown");
    lv_obj_set_style_text_font(printer_status_label, &lv_font_montserrat_18, 0);
    lv_obj_set_style_text_color(printer_status_label, lv_color_white(), 0);
}

static void create_filament_slots(lv_obj_t *parent) {
    lv_obj_t *container = lv_obj_create(parent);
    lv_obj_set_size(container, LV_HOR_RES, LV_VER_RES - 120); // Adjust size to fit between status and bottom bar
    lv_obj_align(container, LV_ALIGN_CENTER, 0, -12);
    lv_obj_set_flex_flow(container, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(container, LV_FLEX_ALIGN_SPACE_AROUND, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_bg_color(container, lv_color_hex(0x000000), 0);
    lv_obj_set_style_border_width(container, 0, 0);
    lv_obj_set_style_pad_all(container, 0, 0);

    // Configurable slot background color for future use
    lv_color_t slot_bg_color = lv_color_hex(0x000000);

    // Create 4 spool slots
    spool_slot_1 = lv_obj_create(container);
    lv_obj_set_size(spool_slot_1, 100, 100);
    lv_obj_set_style_radius(spool_slot_1, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(spool_slot_1, slot_bg_color, 0);
    lv_obj_set_style_border_width(spool_slot_1, 3, 0);
    lv_obj_set_style_border_color(spool_slot_1, lv_color_white(), 0);
    lv_obj_t *label1 = lv_label_create(spool_slot_1);
    lv_label_set_text(label1, "Slot 1");
    lv_obj_set_style_text_color(label1, lv_color_white(), 0);
    lv_obj_set_style_text_font(label1, &lv_font_montserrat_22, 0);
    lv_obj_center(label1);

    spool_slot_2 = lv_obj_create(container);
    lv_obj_set_size(spool_slot_2, 100, 100);
    lv_obj_set_style_radius(spool_slot_2, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(spool_slot_2, slot_bg_color, 0);
    lv_obj_set_style_border_width(spool_slot_2, 3, 0);
    lv_obj_set_style_border_color(spool_slot_2, lv_color_white(), 0);
    lv_obj_t *label2 = lv_label_create(spool_slot_2);
    lv_label_set_text(label2, "Slot 2");
    lv_obj_set_style_text_color(label2, lv_color_white(), 0);
    lv_obj_set_style_text_font(label2, &lv_font_montserrat_22, 0);
    lv_obj_center(label2);

    spool_slot_3 = lv_obj_create(container);
    lv_obj_set_size(spool_slot_3, 100, 100);
    lv_obj_set_style_radius(spool_slot_3, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(spool_slot_3, slot_bg_color, 0);
    lv_obj_set_style_border_width(spool_slot_3, 3, 0);
    lv_obj_set_style_border_color(spool_slot_3, lv_color_white(), 0);
    lv_obj_t *label3 = lv_label_create(spool_slot_3);
    lv_label_set_text(label3, "Slot 3");
    lv_obj_set_style_text_color(label3, lv_color_white(), 0);
    lv_obj_set_style_text_font(label3, &lv_font_montserrat_22, 0);
    lv_obj_center(label3);

    spool_slot_4 = lv_obj_create(container);
    lv_obj_set_size(spool_slot_4, 100, 100);
    lv_obj_set_style_radius(spool_slot_4, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(spool_slot_4, slot_bg_color, 0);
    lv_obj_set_style_border_width(spool_slot_4, 3, 0);
    lv_obj_set_style_border_color(spool_slot_4, lv_color_white(), 0);
    lv_obj_t *label4 = lv_label_create(spool_slot_4);
    lv_label_set_text(label4, "Slot 4");
    lv_obj_set_style_text_color(label4, lv_color_white(), 0);
    lv_obj_set_style_text_font(label4, &lv_font_montserrat_22, 0);
    lv_obj_center(label4);
}

static void create_bottom_bar(lv_obj_t *parent) {
    lv_obj_t *bottom_container = lv_obj_create(parent);
    lv_obj_set_size(bottom_container, LV_HOR_RES, 75);
    lv_obj_align(bottom_container, LV_ALIGN_BOTTOM_MID, 0, 0);
    lv_obj_set_style_bg_color(bottom_container, lv_color_hex(0x000000), 0);
    lv_obj_set_style_border_width(bottom_container, 0, 0);
    lv_obj_set_style_pad_all(bottom_container, 0, 0);
    lv_obj_set_flex_flow(bottom_container, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(bottom_container, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);


    settings_button = lv_btn_create(bottom_container);
    lv_obj_set_size(settings_button, 150, 50);
    lv_obj_add_event_cb(settings_button, settings_button_event_handler, LV_EVENT_CLICKED, NULL);

    settings_button_label = lv_label_create(settings_button);
    lv_label_set_text(settings_button_label, "Settings");
    lv_obj_center(settings_button_label);
}

static void settings_button_event_handler(lv_event_t *e) {
    // Event handler for settings button
    // For now, it does nothing
}
