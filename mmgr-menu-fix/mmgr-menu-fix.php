<?php
/**
 * Plugin Name: MMGR Useful Information Menu
 * Description: Repoints the "Useful Information" menu at the category archives, so every entry lists its posts the way it does on mmgr.info. Adds a page under Tools with a preview, an Apply button and an Undo button.
 * Version: 1.0.0
 * Author: Anirudha Talmale
 * License: GPL-2.0-or-later
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/**
 * The menu as mmgr.info builds it: every entry under "Useful Information" is a
 * category archive, which is why the posts show up there without anyone having
 * to maintain a page per section. The new site was built with blank Pages in
 * those slots instead, so the links resolve but there is nothing behind them.
 *
 * Slugs, not IDs - the IDs differ between the two sites, the slugs do not.
 */
const MMGR_MENU_TREE = array(
	array(
		'label'    => 'FAQ - Frequently Asked Questions',
		'slug'     => 'faq',
		'children' => array(
			array( 'label' => 'FAQ', 'slug' => 'faq-2' ),
			array( 'label' => 'Website', 'slug' => 'website' ),
		),
	),
	array(
		'label'    => "Owners' Information",
		'slug'     => 'owners-information',
		'children' => array(
			array( 'label' => 'Building Works', 'slug' => 'building-works' ),
			array( 'label' => "Committee's Contact Details", 'slug' => 'committees-contact-details' ),
			array( 'label' => 'Community Support', 'slug' => 'community-support-opening-hours' ),
			array( 'label' => 'Debtors', 'slug' => 'debtors' ),
			array( 'label' => 'Forms', 'slug' => 'forms' ),
			array( 'label' => 'General Meetings', 'slug' => 'general-meetings-useful-information' ),
			array( 'label' => 'Helpful Contact Details', 'slug' => 'helpful-contact-details' ),
			array( 'label' => 'INMHO', 'slug' => 'mare-nostrum-opening-hours' ),
			array( 'label' => 'MMGR App', 'slug' => 'mmgr-app' ),
			array( 'label' => 'Pools', 'slug' => 'pools-useful-information' ),
			array( 'label' => 'Property Maintenance', 'slug' => 'property-maintenance-useful-information' ),
		),
	),
	array(
		'label'    => 'Other Information',
		'slug'     => 'other-information',
		'children' => array(
			array( 'label' => 'Animal Welfare', 'slug' => 'animal-welfare-useful-information' ),
			array( 'label' => 'General Information', 'slug' => 'general-information-useful-information' ),
			array( 'label' => 'Murcia Region', 'slug' => 'murcia-region-useful-information' ),
			array( 'label' => 'Opening Hours', 'slug' => 'opening-hours' ),
			array( 'label' => 'Resort Maps', 'slug' => 'resort-maps' ),
			array( 'label' => 'Spanish Newspapers', 'slug' => 'spanish-newspapers' ),
			array( 'label' => 'Useful Links', 'slug' => 'useful-links' ),
		),
	),
	array(
		'label'    => 'Services',
		'slug'     => 'services',
		'children' => array(
			array( 'label' => 'Gardening', 'slug' => 'gardening' ),
			array( 'label' => 'Pest Control', 'slug' => 'pest-control-2' ),
			array( 'label' => 'Security', 'slug' => 'security-useful-information' ),
			array( 'label' => 'Telecommunications', 'slug' => 'telecommunications' ),
			array( 'label' => 'Other Services', 'slug' => 'services-3' ),
		),
	),
	array(
		'label'    => 'Social & Sports',
		'slug'     => 'social-sports',
		'children' => array(
			array( 'label' => 'Social Activities', 'slug' => 'social-activities' ),
			array( 'label' => 'Golf', 'slug' => 'golf' ),
			array( 'label' => 'Other Sports', 'slug' => 'sports' ),
		),
	),
);

const MMGR_MENU_BACKUP_OPTION = 'mmgr_menu_fix_backup';
const MMGR_MENU_PARENT_LABELS = array( 'useful information', 'useful info' );

/* -------------------------------------------------------------------------
 * Reading the current menu
 * ---------------------------------------------------------------------- */

/**
 * Every nav menu on the site, newest first, with the one used by the theme's
 * primary location first. The site has more than one menu registered and only
 * one of them is the header, so guessing by name would be a coin toss.
 */
function mmgr_menu_candidates() {
	$menus     = wp_get_nav_menus();
	$locations = get_nav_menu_locations();
	$preferred = array();

	foreach ( array( 'menu_main', 'primary', 'main', 'header' ) as $slot ) {
		if ( ! empty( $locations[ $slot ] ) ) {
			$preferred[] = (int) $locations[ $slot ];
		}
	}
	$preferred = array_merge( $preferred, array_map( 'intval', array_values( $locations ) ) );

	usort(
		$menus,
		static function ( $a, $b ) use ( $preferred ) {
			$rank_a = array_search( (int) $a->term_id, $preferred, true );
			$rank_b = array_search( (int) $b->term_id, $preferred, true );
			$rank_a = ( false === $rank_a ) ? PHP_INT_MAX : $rank_a;
			$rank_b = ( false === $rank_b ) ? PHP_INT_MAX : $rank_b;
			return $rank_a <=> $rank_b;
		}
	);

	return $menus;
}

/** The "Useful Information" item in a menu, or null if it has none. */
function mmgr_find_parent_item( $menu_id ) {
	$items = wp_get_nav_menu_items( $menu_id );
	if ( ! $items ) {
		return null;
	}
	foreach ( $items as $item ) {
		$title = strtolower( trim( html_entity_decode( $item->title, ENT_QUOTES, 'UTF-8' ) ) );
		if ( in_array( $title, MMGR_MENU_PARENT_LABELS, true ) && 0 === (int) $item->menu_item_parent ) {
			return $item;
		}
	}
	return null;
}

/**
 * Every descendant of an item, at any depth.
 *
 * The subtree has to be collected before anything is deleted - deleting a
 * parent first orphans its children, and they then survive as stray top level
 * entries in the menu rather than disappearing with it.
 */
function mmgr_collect_subtree( $items, $parent_id ) {
	$found = array();
	foreach ( $items as $item ) {
		if ( (int) $item->menu_item_parent === (int) $parent_id ) {
			$found[] = $item;
			$found   = array_merge( $found, mmgr_collect_subtree( $items, $item->ID ) );
		}
	}
	return $found;
}

/* -------------------------------------------------------------------------
 * Planning
 * ---------------------------------------------------------------------- */

/**
 * Work out what would be built, without building it.
 *
 * Returned so the admin page can show the plan before anything is touched, and
 * so a missing category shows up as a warning rather than as a silently absent
 * menu entry after the fact.
 */
function mmgr_build_plan() {
	$plan    = array();
	$missing = array();

	foreach ( MMGR_MENU_TREE as $section ) {
		$term = get_term_by( 'slug', $section['slug'], 'category' );
		if ( ! $term ) {
			$missing[] = $section['slug'];
			continue;
		}
		$row = array(
			'label'    => $section['label'],
			'term'     => $term,
			'url'      => get_term_link( $term ),
			'count'    => (int) $term->count,
			'children' => array(),
		);
		foreach ( $section['children'] as $child ) {
			$child_term = get_term_by( 'slug', $child['slug'], 'category' );
			if ( ! $child_term ) {
				$missing[] = $child['slug'];
				continue;
			}
			$row['children'][] = array(
				'label' => $child['label'],
				'term'  => $child_term,
				'url'   => get_term_link( $child_term ),
				'count' => (int) $child_term->count,
			);
		}
		$plan[] = $row;
	}

	return array( 'plan' => $plan, 'missing' => $missing );
}

/* -------------------------------------------------------------------------
 * Applying
 * ---------------------------------------------------------------------- */

/**
 * Replace the "Useful Information" subtree with category archive links.
 *
 * The old items are written to an option first, in the shape wp_update_nav_menu_item
 * wants back, so Undo can put the menu back exactly as it was rather than
 * approximately. Running this twice is safe: the second run removes what the
 * first one built and rebuilds it, and the backup is only written when there is
 * no backup already, so the original menu is never overwritten by a rebuild.
 */
function mmgr_apply( $menu_id ) {
	$parent = mmgr_find_parent_item( $menu_id );
	if ( ! $parent ) {
		return new WP_Error( 'mmgr_no_parent', 'No top level "Useful Information" entry was found in this menu.' );
	}

	$built = mmgr_build_plan();
	if ( ! $built['plan'] ) {
		return new WP_Error( 'mmgr_no_categories', 'None of the expected categories exist on this site, so there is nothing to link to.' );
	}

	$items    = wp_get_nav_menu_items( $menu_id );
	$existing = mmgr_collect_subtree( $items, $parent->ID );

	// Keyed per menu: a theme that has a separate mobile menu needs the fix run
	// on both, and a single shared backup slot would quietly decide the second
	// menu had already been saved and throw its original entries away.
	$backups = get_option( MMGR_MENU_BACKUP_OPTION );
	$backups = is_array( $backups ) ? $backups : array();
	if ( ! isset( $backups[ (int) $menu_id ] ) ) {
		$snapshot = array();
		foreach ( $existing as $item ) {
			$snapshot[] = array(
				'id'          => (int) $item->ID,
				'parent'      => (int) $item->menu_item_parent,
				'order'       => (int) $item->menu_order,
				'title'       => $item->title,
				'type'        => $item->type,
				'object'      => $item->object,
				'object_id'   => (int) $item->object_id,
				'url'         => $item->url,
				'target'      => $item->target,
				'attr_title'  => $item->attr_title,
				'description' => $item->description,
				'classes'     => is_array( $item->classes ) ? implode( ' ', $item->classes ) : '',
				'xfn'         => $item->xfn,
			);
		}
		$backups[ (int) $menu_id ] = array(
			'menu_id'   => (int) $menu_id,
			'parent_id' => (int) $parent->ID,
			'items'     => $snapshot,
		);
		update_option( MMGR_MENU_BACKUP_OPTION, $backups, false );
	}

	foreach ( $existing as $item ) {
		wp_delete_post( $item->ID, true );
	}

	$order   = (int) $parent->menu_order;
	$created = 0;

	foreach ( $built['plan'] as $section ) {
		++$order;
		$section_id = wp_update_nav_menu_item(
			$menu_id,
			0,
			array(
				'menu-item-title'     => $section['label'],
				'menu-item-type'      => 'taxonomy',
				'menu-item-object'    => 'category',
				'menu-item-object-id' => (int) $section['term']->term_id,
				'menu-item-parent-id' => (int) $parent->ID,
				'menu-item-status'    => 'publish',
				'menu-item-position'  => $order,
			)
		);
		if ( is_wp_error( $section_id ) ) {
			return $section_id;
		}
		++$created;

		foreach ( $section['children'] as $child ) {
			++$order;
			$child_id = wp_update_nav_menu_item(
				$menu_id,
				0,
				array(
					'menu-item-title'     => $child['label'],
					'menu-item-type'      => 'taxonomy',
					'menu-item-object'    => 'category',
					'menu-item-object-id' => (int) $child['term']->term_id,
					'menu-item-parent-id' => (int) $section_id,
					'menu-item-status'    => 'publish',
					'menu-item-position'  => $order,
				)
			);
			if ( is_wp_error( $child_id ) ) {
				return $child_id;
			}
			++$created;
		}
	}

	return array( 'created' => $created, 'removed' => count( $existing ), 'missing' => $built['missing'] );
}

/**
 * Put the menu back the way it was.
 *
 * Old and new IDs cannot match, so parents are remapped through the old-to-new
 * table as items are recreated. The snapshot is ordered parent-before-child by
 * wp_get_nav_menu_items, which is what makes that single pass enough.
 */
function mmgr_undo( $menu_id ) {
	$backups = get_option( MMGR_MENU_BACKUP_OPTION );
	$backups = is_array( $backups ) ? $backups : array();
	$backup  = isset( $backups[ (int) $menu_id ] ) ? $backups[ (int) $menu_id ] : null;
	if ( ! $backup || empty( $backup['items'] ) ) {
		return new WP_Error( 'mmgr_no_backup', 'There is no saved copy of this menu to restore.' );
	}

	$menu_id = (int) $backup['menu_id'];
	$parent  = get_post( (int) $backup['parent_id'] );
	if ( ! $parent ) {
		return new WP_Error( 'mmgr_no_parent', 'The "Useful Information" entry the backup belongs to no longer exists.' );
	}

	$items = wp_get_nav_menu_items( $menu_id );
	foreach ( mmgr_collect_subtree( $items, (int) $backup['parent_id'] ) as $item ) {
		wp_delete_post( $item->ID, true );
	}

	$map      = array( (int) $backup['parent_id'] => (int) $backup['parent_id'] );
	$restored = 0;

	foreach ( $backup['items'] as $item ) {
		$new_parent = isset( $map[ $item['parent'] ] ) ? $map[ $item['parent'] ] : (int) $backup['parent_id'];
		$new_id     = wp_update_nav_menu_item(
			$menu_id,
			0,
			array(
				'menu-item-title'       => $item['title'],
				'menu-item-type'        => $item['type'],
				'menu-item-object'      => $item['object'],
				'menu-item-object-id'   => $item['object_id'],
				'menu-item-url'         => $item['url'],
				'menu-item-target'      => $item['target'],
				'menu-item-attr-title'  => $item['attr_title'],
				'menu-item-description' => $item['description'],
				'menu-item-classes'     => $item['classes'],
				'menu-item-xfn'         => $item['xfn'],
				'menu-item-parent-id'   => $new_parent,
				'menu-item-status'      => 'publish',
				'menu-item-position'    => $item['order'],
			)
		);
		if ( is_wp_error( $new_id ) ) {
			return $new_id;
		}
		$map[ $item['id'] ] = (int) $new_id;
		++$restored;
	}

	unset( $backups[ (int) $backup['menu_id'] ] );
	if ( $backups ) {
		update_option( MMGR_MENU_BACKUP_OPTION, $backups, false );
	} else {
		delete_option( MMGR_MENU_BACKUP_OPTION );
	}

	return array( 'restored' => $restored );
}

/* -------------------------------------------------------------------------
 * Admin page
 * ---------------------------------------------------------------------- */

add_action(
	'admin_menu',
	static function () {
		add_management_page(
			'Useful Information menu',
			'Useful Information menu',
			'edit_theme_options',
			'mmgr-menu-fix',
			'mmgr_render_admin_page'
		);
	}
);

function mmgr_render_admin_page() {
	if ( ! current_user_can( 'edit_theme_options' ) ) {
		wp_die( 'You do not have permission to edit menus.' );
	}

	$notice = null;

	if ( isset( $_POST['mmgr_action'] ) && check_admin_referer( 'mmgr_menu_fix' ) ) {
		$action  = sanitize_text_field( wp_unslash( $_POST['mmgr_action'] ) );
		$menu_id = isset( $_POST['mmgr_menu_id'] ) ? (int) $_POST['mmgr_menu_id'] : 0;

		if ( 'apply' === $action ) {
			$result = mmgr_apply( $menu_id );
		} elseif ( 'undo' === $action ) {
			$result = mmgr_undo( $menu_id );
		} else {
			$result = new WP_Error( 'mmgr_unknown', 'Unknown action.' );
		}

		if ( is_wp_error( $result ) ) {
			$notice = array( 'error', $result->get_error_message() );
		} elseif ( isset( $result['restored'] ) ) {
			$notice = array( 'success', sprintf( 'Menu restored - %d entries put back as they were.', $result['restored'] ) );
		} else {
			$text = sprintf(
				'Done - %d category links created, %d old entries removed.',
				$result['created'],
				$result['removed']
			);
			if ( $result['missing'] ) {
				$text .= ' Not found on this site, so skipped: ' . implode( ', ', $result['missing'] ) . '.';
			}
			$notice = array( 'success', $text );
		}
	}

	$menus   = mmgr_menu_candidates();
	$built   = mmgr_build_plan();
	$backups = get_option( MMGR_MENU_BACKUP_OPTION );
	$backups = is_array( $backups ) ? $backups : array();

	// Keep whichever menu was just acted on selected, so a site with a separate
	// mobile menu does not bounce back to the first one between the two runs.
	$menu_id = $menus ? (int) $menus[0]->term_id : 0;
	if ( isset( $_REQUEST['mmgr_menu_id'] ) ) {
		$menu_id = (int) $_REQUEST['mmgr_menu_id'];
	}
	$parent = $menu_id ? mmgr_find_parent_item( $menu_id ) : null;

	echo '<div class="wrap"><h1>Useful Information menu</h1>';

	if ( $notice ) {
		printf(
			'<div class="notice notice-%s"><p>%s</p></div>',
			esc_attr( $notice[0] ),
			esc_html( $notice[1] )
		);
	}

	echo '<p>The entries under <strong>Useful Information</strong> currently point at blank pages. '
		. 'This replaces them with links to the matching categories, which is how the same menu is '
		. 'built on mmgr.info, so each one lists its posts automatically.</p>';

	if ( ! $menus ) {
		echo '<p><strong>No navigation menus were found on this site.</strong></p></div>';
		return;
	}

	echo '<form method="post">';
	wp_nonce_field( 'mmgr_menu_fix' );

	echo '<p><label for="mmgr_menu_id"><strong>Menu to change:</strong></label> ';
	// Re-submits with no mmgr_action, so switching menus only redraws the page.
	echo '<select name="mmgr_menu_id" id="mmgr_menu_id" onchange="this.form.submit()">';
	foreach ( $menus as $menu ) {
		$has = mmgr_find_parent_item( $menu->term_id ) ? '' : ' - no "Useful Information" entry';
		printf(
			'<option value="%d"%s>%s%s</option>',
			(int) $menu->term_id,
			selected( $menu_id, (int) $menu->term_id, false ),
			esc_html( $menu->name ),
			esc_html( $has )
		);
	}
	echo '</select></p>';

	if ( $built['missing'] ) {
		printf(
			'<div class="notice notice-warning inline"><p>These categories are not on this site and will be skipped: %s</p></div>',
			esc_html( implode( ', ', $built['missing'] ) )
		);
	}

	echo '<h2>What will be created</h2><table class="widefat striped" style="max-width:60em">';
	echo '<thead><tr><th>Menu entry</th><th>Links to</th><th style="width:6em">Posts</th></tr></thead><tbody>';
	foreach ( $built['plan'] as $section ) {
		printf(
			'<tr><td><strong>%s</strong></td><td><a href="%s">%s</a></td><td>%d</td></tr>',
			esc_html( $section['label'] ),
			esc_url( $section['url'] ),
			esc_html( $section['url'] ),
			(int) $section['count']
		);
		foreach ( $section['children'] as $child ) {
			printf(
				'<tr><td style="padding-left:2.5em">%s</td><td><a href="%s">%s</a></td><td>%d</td></tr>',
				esc_html( $child['label'] ),
				esc_url( $child['url'] ),
				esc_html( $child['url'] ),
				(int) $child['count']
			);
		}
	}
	echo '</tbody></table>';

	if ( $parent ) {
		$items    = wp_get_nav_menu_items( $menu_id );
		$existing = mmgr_collect_subtree( $items, $parent->ID );
		printf(
			'<p>%d entries currently sit under "Useful Information" and will be replaced. '
			. 'A copy is kept, so this can be undone.</p>',
			count( $existing )
		);
	}

	echo '<p><button type="submit" name="mmgr_action" value="apply" class="button button-primary">'
		. 'Rebuild the Useful Information menu</button>';

	if ( isset( $backups[ $menu_id ] ) ) {
		echo ' <button type="submit" name="mmgr_action" value="undo" class="button">'
			. 'Undo - put the old menu back</button>';
	}

	echo '</p></form></div>';
}
