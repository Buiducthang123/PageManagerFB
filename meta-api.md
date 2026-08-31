# Manage a Page



This document shows you how to perform the following tasks for a Facebook Page:

* Get a list of pages that you can perform a task on including:
    * Specific tasks you can perform on each Page
    * Page access tokens for each Page that you can use to test API calls
* Get and update details about a Page
* Get and update settings for a Page
* Get notifications about suggested changes Meta will be implementing on a Page
    * Accept or reject these suggested changes
* Get reviews for a Page
* Block a person from a Page

## Before you start

This guide assumes you have read the [Pages API Overview](https://developers.facebook.com/documentation/pages-api/overview).

For a person who can perform tasks on the page, you will need to implement Facebook Login for Business to ask for the following permissions and receive a User or Page access token:

* `pages_manage_engagement`
* `pages_manage_metadata`
* `pages_manage_posts`  
* `pages_read_engagement`
* `pages_read_user_engagement`
* `pages_show_list`
* `publish_video` permission, if you are publishing a video to the Page

If using a business system user in your API requests, the `business_management` permission is required.

Your app user must be able to perform the `CREATE_CONTENT`, `MANAGE`, and/or `MODERATE` tasks on the Page in the API requests.

### Best practices

When testing an API call, you can include the `access_token` parameter set to your access token. However, when making secure calls from your app, use the [access token class.](https://developers.facebook.com/documentation/facebook-login/guides/access-tokens#portabletokens)

*Example requests are formatted for readability. Replace **bold, italics values**, such as **page_id**, with your values.*

## Pages, tasks, and tokens

A single API call can give you a lot of information for Pages on which you can perform a task.

### Get your Pages

To get a list of all Pages on which you can perform tasks, the tasks you can perform on each page, and a short-lived Page access token for each Page, send a `GET` request to `/user_id/accounts` endpoint using a User access token.

#### Example request

```curl
curl -i -X GET
     "https://graph.facebook.com/user_id/accounts"
```

On success, your app will receive a JSON response with an array of Page objects. Each Page object contains:

* The name for the Page
* The ID for the Page
* The Page category, category name and ID
* A short-lived Page access token
* All tasks the user can perform on the Page

#### Example response

```json
{
  "data": [
    {
      "access_token": "{facebook-for-developers-page-access-token}",
      "category": "Internet Company",
      "category_list": [
        {
          "id": "2256",
          "name": "Internet Company"
        }
      ],
      "name": "Facebook for Developers",
      "id": "{facebook-for-developers-page-id}",
      "tasks": [
        "ANALYZE",
        "ADVERTISE",
        "MODERATE",
        "CREATE_CONTENT"
      ]
    },
    {
      "access_token": "{my-outlandish-stories-page-access-token}",
      "category": "Blogger",
      "category_list": [
        {
          "id": "361282040719868",
          "name": "Blogger"
        }
      ],
      "name": "My Outlandish Stories",
      "id": "{my-outlandish-stories-page-id}",
      "tasks": [
        "ANALYZE",
        "ADVERTISE",
        "MODERATE",
        "CREATE_CONTENT",
        "MANAGE"
      ]
    },
...
  ]
}
```

### Get tasks for others

If you can perform the `MANAGE` task on the Page, you can get a list of other people who can perform tasks on that Page including the tasks each person can perform.

To get a list of people and the tasks they can perform on the Page, send a `GET` request to the `/page_id/roles` endpoint.

#### Example request

```curl
curl -i -X GET "https://graph.facebook.com/page_id/roles"
```

On success, your app receives a JSON response with the person's name, their Page-scoped ID, and tasks each person can perform on a Page.

#### Example response

```json
{
  "data": [
    {
      "name": "Person One",
      "id": "page_scoped_id_for_one"
        "tasks": [
          "ANALYZE"
        ]
    },
    {
      "name": "Person Two",
      "id": "page_scoped_id_for_two",
      "tasks": [
        "ANALYZE",
        "ADVERTISE",
        "MODERATE",
        "CREATE_CONTENT",
        "MANAGE"
      ]
    },
...
  ],
}
```

## Page details

If you can perform the `MANAGE` task on the Page, you can use a Page access token or if your app has been approved for the Page Public Content Access feature, you can use a User access token, to view details for a Page such as about, email, hours of operation, etc.

### Get details

To get details about a Page, send a `GET` request to the `/page_id` endpoint with the `fields` parameter set to the Page details you would like to view.

**Note:** You can use the `/pages/search` endpoint to find Page IDs when using the Page Public Content Access feature.

#### Example request

```curl
curl -i -X GET "https://graph.facebook.com/page_id\
    ?fields=about,attire,bio,location,parking,hours,emails,website"
```

On success, your app receives a JSON response with value for the fields you requested.  If a field is not returned in the response, the Page does not have this value set. For example, if the Page has not set the `attire` field, this field will not be returned in the response.

### Update details

If you can perform the `MANAGE` task on the Page, you can use a Page access token to send a `POST` request to the `/page_id` endpoint with the parameters that you want to update, such as the `about` parameter.

#### Example request

```curl
curl -i -X POST "https://graph.facebook.com/v25.0/page_id" \
     -H "Content-Type: application/json" \
     -d '{
           "about":"This is an awesome cafe located downtown!",
         }'
```

On success, your app will receive a JSON response with `success` set to `true`.

### Meta proposed changes

On occassion, Meta will propose changes to the details for your Page, such as fixing a typo, or updating the categories on your Page to help people better find your Page. In order to get these notifications, you must be subscribed to the `page_upcoming_change` and/or the `page_change_proposal` webhook.

Once you receive the notification, you can do one of the following:

* Do nothing and the changes will take affect at the time designated in the notification
* Actively accept the changes and the changes will take affect immediately
* Actively reject the changes and no changes will be made

#### Accept or reject a proposed change

To actively accept or reject a proposed change, send a `POST` request to the `/page_change_proposal_id` endpoint with the `accept` field set to `true` to accept the change or `false` to reject it.  The `page_change_proposal_id` is the `proposal.id` value you received in the `page_upcoming_change` webhook notification or `value.id` value you received in the `page_change_proposal` webhook notification.

```
curl -i -X POST "https://graph.facebook.com/v25.0/page_change_proposal_id" \
     -H "Content-Type: application/json" \
     -d '{
           "accept":"true",
         }'
```

On success, your app receives a JSON response with `success` set to `true`.

## Page settings {#update_settings}

If you can perform the `MANAGE` task on the Page, you can use a Page access token to send a `GET` request to the `/page_id/settings` endpoint to get a list of all the settings for that Page.

#### Example request

```curl
curl -i -X GET "https://graph.facebook.com/v25.0/page_id/settings"
```

On success, your app will receive a JSON response with an array of objects where each object is the `setting` set to a Page setting and the value, either `true` or `false`.

#### Example response

```json
{
  "data": [
    {
      "setting": "USERS_CAN_POST",
      "value": false
    },
    {
      "setting": "USERS_CAN_MESSAGE",
      "value": true
    },
    {
      "setting": "USERS_CAN_POST_PHOTOS",
      "value": true
    },
    ...
  ]
}
```

### Update a setting

To update the settings for a Page, send a `POST` request to the `/page_id/settings` endpoint with the `option` parameter set to the setting you want to update.

#### Example request

```
curl -i -X POST "https://graph.facebook.com/v25.0/page_id/settings" \
     -H "Content-Type: application/json" \
     -d '{
           "option":{"USERS_CAN_MESSAGE": "true"},
         }'
```

On success, your app receives a JSON response with `success` set to `true`.

## Get reviews

You can get reviews for a Page, including the name of the reviewer, their Page-scoped ID, whether it was a positive or negative recommendation, and the review text, send a `GET` request to the `/page_id/ratings` endpoint.

#### Example request

```curl
curl -i -X GET "https://graph.facebook.com/page_id/ratings"
```

On success, your app receives a JSON array with review objects. Each object contains:

* `created_time` set to the time the review was created,
* `recommendation_type` set to `positive` or `negative`
* `review_text` set to the content for the review
* a `reviewer` object with the `name` and `id` for the person who wrote that review

```
{
  "data": [
    {
      "created_time": "unixtimestamp",
      "recommendation_type": "positive",
      "review_text": "I love this page!",
      "reviewer": {
        "name": "Person One",
        "id": "psid_for_one"
      }
    },
    {
      "created_time": "unixtimestamp",
      "recommendation_type": "positive",
      "review_text": "This page is wonderful!",
      "reviewer": {
        "name": "Person Two",
        "id": "psid_for_two"
      }
    },
...
  ]
}
```

## Block a person

To block a person from commenting on a Page, send a `POST` request to the `/page_id/blocked` endpoint with the `user` parameter set to Page-scoped ID for the person you want to block.

#### Example request

```curl
curl -i -X POST "https://graph.facebook.com/v25.0/page_id/blocked"
     -H "Content-Type: application/json" \
     -d '{
           "user":"psid_to_block",
         }'
```

On success, your app receives a JSON response with the Page-scoped ID set to `true`.

```
{
 "psid_to_block": true
}
```

## Next steps

Learn how to [publish links, photos, and videos to your Page](https://developers.facebook.com/documentation/pages-api/posts).

## See also

- [Meta Webhooks for Pages](https://developers.facebook.com/docs/graph-api/webhooks/getting-started/webhooks-for-pages)

- [Meta Business Help Center – Business System User](https://www.facebook.com/business/help/327596604689624)

#### References

- [Page Reference](https://developers.facebook.com/docs/graph-api/reference/page)

- [Page Blocked Reference](https://developers.facebook.com/docs/graph-api/reference/page/blocked)

- [Page Feed Reference](https://developers.facebook.com/docs/graph-api/reference/page/feed)

- [Page Post Reference](https://developers.facebook.com/docs/graph-api/reference/page-post)

- [Page Settings](https://developers.facebook.com/docs/graph-api/reference/page/settings)

- [Page Upcoming Change Reference](https://developers.facebook.com/docs/graph-api/reference/page-upcoming-change)

- [Permissions Reference](https://developers.facebook.com/docs/permissions)

- [User Accounts Reference](https://developers.facebook.com/docs/graph-api/reference/user/accounts)

