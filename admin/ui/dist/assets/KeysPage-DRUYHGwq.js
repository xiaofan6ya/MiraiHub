import{h as pt,k as v,i as la,m as Ia,F as Ne,c as S,r as h,w as Ve,o as ua,D as Aa,j as $a,H as La,G as Ea,I as Ka,n as At,p as Oa,t as ce,b as Ba,V as ee,R as c,Q as m,S as i,$ as Na,O as L,f as N,W as y,X as D,Z as rt,P as re,Y as he}from"./vendor-vue-01Qz8dz3.js";import{J as Ma,K as ft,L as Wa,l as Fa,M as ja,r as qa,O as Ua,v as Ha,Q as Da,m as p,n as C,p as O,o as U,q as Va,R as $t,T as Dt,w as Ga,x as da,V as Xa,W as Qa,A as fe,z as ot,P as Ja,H as Vt,N as we,X as Ya,C as Za,I as en}from"./index-DKTTgY9I.js";import{u as tn,B as J,E as st,a as an}from"./FormField.vue_vue_type_style_index_0_scoped_ab743373_lang-EAkmMKmL.js";import{I as nn}from"./IconBtn-C4Y-Gyjr.js";import{C as Gt}from"./Card-BR8wSR71.js";import{S as it}from"./Section-BsL4QYVc.js";import{B as lt}from"./Badge-BofLl5Fa.js";/* empty css                                                                  */import{C as Xt}from"./Chip-uY2U7-4N.js";import{C as ut}from"./Copyable-WkUmfWy7.js";import{S as rn}from"./Segmented-BMV1Ag6Q.js";import{K as on}from"./KV-BZQHBhCd.js";import{C as Se}from"./CodeBlock-DBAFt1X5.js";import{D as Qt,N as Lt}from"./DataTable-D1vCpovv.js";/* empty css                                                              */import{F as sn}from"./FilterBar-C3O-93Zs.js";import{M as Jt}from"./Meter-Daah72D4.js";import{R as ln}from"./RowActions-BjJPjPmO.js";import{h as te,j as un,A as dn,_ as cn}from"./_plugin-vue_export-helper-ND5k2sci.js";import{F as fn}from"./FormDrawer-ZT9E05DZ.js";import{F as pe}from"./FormField-4JKw0b1G.js";import{D as dt}from"./DetailDrawer-drQ1Enhf.js";import{e as Et,f as Oe}from"./error-Bz9KZGxV.js";import{u as pn}from"./useConfirm-B_vFXYQ5.js";import{u as vn}from"./useTableQuery-DG7Bhkkg.js";import{F as Me,f as ca,e as Te,m as bn,c as xe}from"./format-ByabA-4O.js";import{R as mn}from"./refresh-cw-BJQFgCC5.js";import{P as Yt}from"./plus-D64UX1bf.js";import{A as gn}from"./Add-BYamtQox.js";import{V as Kt,E as yn,q as hn,G as xn,v as Zt,j as kn,g as ct}from"./vendor-naive-SrciwSnN.js";import{N as Be}from"./Select-CKSTt_jz.js";import{N as ea}from"./Skeleton-B6V_ycE-.js";/* empty css                                                                */import"./Spin-DO6M_ocp.js";import"./feedback-uo15N4QT.js";/* empty css                                                                 */import"./media-DhhT9Mh5.js";import"./Forward-CyWRo3OE.js";import"./Dropdown-BvlPWbkL.js";import"./x-DOgpPoAN.js";import"./ellipsis-x9MhPzzH.js";var _n=/\s/;function Cn(e){for(var n=e.length;n--&&_n.test(e.charAt(n)););return n}var wn=/^\s+/;function Sn(e){return e&&e.slice(0,Cn(e)+1).replace(wn,"")}var ta=NaN,Tn=/^[-+]0x[0-9a-f]+$/i,Pn=/^0b[01]+$/i,Rn=/^0o[0-7]+$/i,zn=parseInt;function aa(e){if(typeof e=="number")return e;if(Ma(e))return ta;if(ft(e)){var n=typeof e.valueOf=="function"?e.valueOf():e;e=ft(n)?n+"":n}if(typeof e!="string")return e===0?e:+e;e=Sn(e);var r=Pn.test(e);return r||Rn.test(e)?zn(e.slice(2),r?2:8):Tn.test(e)?ta:+e}var Ot=function(){return Wa.Date.now()},In="Expected a function",An=Math.max,$n=Math.min;function Ln(e,n,r){var d,o,f,g,k,x,P=0,z=!1,$=!1,j=!0;if(typeof e!="function")throw new TypeError(In);n=aa(n)||0,ft(r)&&(z=!!r.leading,$="maxWait"in r,f=$?An(aa(r.maxWait)||0,n):f,j="trailing"in r?!!r.trailing:j);function M(_){var F=d,G=o;return d=o=void 0,P=_,g=e.apply(G,F),g}function B(_){return P=_,k=setTimeout(W,n),z?M(_):g}function K(_){var F=_-x,G=_-P,ve=n-F;return $?$n(ve,f-G):ve}function q(_){var F=_-x,G=_-P;return x===void 0||F>=n||F<0||$&&G>=f}function W(){var _=Ot();if(q(_))return E(_);k=setTimeout(W,K(_))}function E(_){return k=void 0,j&&d?M(_):(d=o=void 0,g)}function ae(){k!==void 0&&clearTimeout(k),P=0,d=x=o=k=void 0}function V(){return k===void 0?g:E(Ot())}function T(){var _=Ot(),F=q(_);if(d=arguments,o=this,x=_,F){if(k===void 0)return B(x);if($)return clearTimeout(k),k=setTimeout(W,n),M(x)}return k===void 0&&(k=setTimeout(W,n)),g}return T.cancel=ae,T.flush=V,T}var En="Expected a function";function Kn(e,n,r){var d=!0,o=!0;if(typeof e!="function")throw new TypeError(En);return ft(r)&&(d="leading"in r?!!r.leading:d,o="trailing"in r?!!r.trailing:o),Ln(e,n,{leading:d,maxWait:n,trailing:o})}const qt=Fa("n-tabs"),fa={tab:[String,Number,Object,Function],name:{type:[String,Number],required:!0},disabled:Boolean,displayDirective:{type:String,default:"if"},closable:{type:Boolean,default:void 0},tabProps:Object,label:[String,Number,Object,Function]},De=pt({__TAB_PANE__:!0,name:"TabPane",alias:["TabPanel"],props:fa,slots:Object,setup(e){const n=la(qt,null);return n||ja("tab-pane","`n-tab-pane` must be placed inside `n-tabs`."),{style:n.paneStyleRef,class:n.paneClassRef,mergedClsPrefix:n.mergedClsPrefixRef}},render(){return v("div",{class:[`${this.mergedClsPrefix}-tab-pane`,this.class],style:this.style},this.$slots)}}),On=Object.assign({internalLeftPadded:Boolean,internalAddable:Boolean,internalCreatedByPane:Boolean},Da(fa,["displayDirective"])),Ft=pt({__TAB__:!0,inheritAttrs:!1,name:"Tab",props:On,setup(e){const{mergedClsPrefixRef:n,valueRef:r,typeRef:d,closableRef:o,tabStyleRef:f,addTabStyleRef:g,tabClassRef:k,addTabClassRef:x,tabChangeIdRef:P,onBeforeLeaveRef:z,triggerRef:$,handleAdd:j,activateTab:M,handleClose:B}=la(qt);return{trigger:$,mergedClosable:S(()=>{if(e.internalAddable)return!1;const{closable:K}=e;return K===void 0?o.value:K}),style:f,addStyle:g,tabClass:k,addTabClass:x,clsPrefix:n,value:r,type:d,handleClose(K){K.stopPropagation(),!e.disabled&&B(e.name)},activateTab(){if(e.disabled)return;if(e.internalAddable){j();return}const{name:K}=e,q=++P.id;if(K!==r.value){const{value:W}=z;W?Promise.resolve(W(e.name,r.value)).then(E=>{E&&P.id===q&&M(K)}):M(K)}}}},render(){const{internalAddable:e,clsPrefix:n,name:r,disabled:d,label:o,tab:f,value:g,mergedClosable:k,trigger:x,$slots:{default:P}}=this,z=o??f;return v("div",{class:`${n}-tabs-tab-wrapper`},this.internalLeftPadded?v("div",{class:`${n}-tabs-tab-pad`}):null,v("div",Object.assign({key:r,"data-name":r,"data-disabled":d?!0:void 0},Ia({class:[`${n}-tabs-tab`,g===r&&`${n}-tabs-tab--active`,d&&`${n}-tabs-tab--disabled`,k&&`${n}-tabs-tab--closable`,e&&`${n}-tabs-tab--addable`,e?this.addTabClass:this.tabClass],onClick:x==="click"?this.activateTab:void 0,onMouseenter:x==="hover"?this.activateTab:void 0,style:e?this.addStyle:this.style},this.internalCreatedByPane?this.tabProps||{}:this.$attrs)),v("span",{class:`${n}-tabs-tab__label`},e?v(Ne,null,v("div",{class:`${n}-tabs-tab__height-placeholder`}," "),v(qa,{clsPrefix:n},{default:()=>v(gn,null)})):P?P():typeof z=="object"?z:Ua(z??r)),k&&this.type==="card"?v(Ha,{clsPrefix:n,class:`${n}-tabs-tab__close`,onClick:this.handleClose,disabled:d}):null))}}),Bn=p("tabs",`
 box-sizing: border-box;
 width: 100%;
 display: flex;
 flex-direction: column;
 transition:
 background-color .3s var(--n-bezier),
 border-color .3s var(--n-bezier);
`,[C("segment-type",[p("tabs-rail",[O("&.transition-disabled",[p("tabs-capsule",`
 transition: none;
 `)])])]),C("top",[p("tab-pane",`
 padding: var(--n-pane-padding-top) var(--n-pane-padding-right) var(--n-pane-padding-bottom) var(--n-pane-padding-left);
 `)]),C("left",[p("tab-pane",`
 padding: var(--n-pane-padding-right) var(--n-pane-padding-bottom) var(--n-pane-padding-left) var(--n-pane-padding-top);
 `)]),C("left, right",`
 flex-direction: row;
 `,[p("tabs-bar",`
 width: 2px;
 right: 0;
 transition:
 top .2s var(--n-bezier),
 max-height .2s var(--n-bezier),
 background-color .3s var(--n-bezier);
 `),p("tabs-tab",`
 padding: var(--n-tab-padding-vertical); 
 `)]),C("right",`
 flex-direction: row-reverse;
 `,[p("tab-pane",`
 padding: var(--n-pane-padding-left) var(--n-pane-padding-top) var(--n-pane-padding-right) var(--n-pane-padding-bottom);
 `),p("tabs-bar",`
 left: 0;
 `)]),C("bottom",`
 flex-direction: column-reverse;
 justify-content: flex-end;
 `,[p("tab-pane",`
 padding: var(--n-pane-padding-bottom) var(--n-pane-padding-right) var(--n-pane-padding-top) var(--n-pane-padding-left);
 `),p("tabs-bar",`
 top: 0;
 `)]),p("tabs-rail",`
 position: relative;
 padding: 3px;
 border-radius: var(--n-tab-border-radius);
 width: 100%;
 background-color: var(--n-color-segment);
 transition: background-color .3s var(--n-bezier);
 display: flex;
 align-items: center;
 `,[p("tabs-capsule",`
 border-radius: var(--n-tab-border-radius);
 position: absolute;
 pointer-events: none;
 background-color: var(--n-tab-color-segment);
 box-shadow: 0 1px 3px 0 rgba(0, 0, 0, .08);
 transition: transform 0.3s var(--n-bezier);
 `),p("tabs-tab-wrapper",`
 flex-basis: 0;
 flex-grow: 1;
 display: flex;
 align-items: center;
 justify-content: center;
 `,[p("tabs-tab",`
 overflow: hidden;
 border-radius: var(--n-tab-border-radius);
 width: 100%;
 display: flex;
 align-items: center;
 justify-content: center;
 `,[C("active",`
 font-weight: var(--n-font-weight-strong);
 color: var(--n-tab-text-color-active);
 `),O("&:hover",`
 color: var(--n-tab-text-color-hover);
 `)])])]),C("flex",[p("tabs-nav",`
 width: 100%;
 position: relative;
 `,[p("tabs-wrapper",`
 width: 100%;
 `,[p("tabs-tab",`
 margin-right: 0;
 `)])])]),p("tabs-nav",`
 box-sizing: border-box;
 line-height: 1.5;
 display: flex;
 transition: border-color .3s var(--n-bezier);
 `,[U("prefix, suffix",`
 display: flex;
 align-items: center;
 `),U("prefix","padding-right: 16px;"),U("suffix","padding-left: 16px;")]),C("top, bottom",[O(">",[p("tabs-nav",[p("tabs-nav-scroll-wrapper",[O("&::before",`
 top: 0;
 bottom: 0;
 left: 0;
 width: 20px;
 `),O("&::after",`
 top: 0;
 bottom: 0;
 right: 0;
 width: 20px;
 `),C("shadow-start",[O("&::before",`
 box-shadow: inset 10px 0 8px -8px rgba(0, 0, 0, .12);
 `)]),C("shadow-end",[O("&::after",`
 box-shadow: inset -10px 0 8px -8px rgba(0, 0, 0, .12);
 `)])])])])]),C("left, right",[p("tabs-nav-scroll-content",`
 flex-direction: column;
 `),O(">",[p("tabs-nav",[p("tabs-nav-scroll-wrapper",[O("&::before",`
 top: 0;
 left: 0;
 right: 0;
 height: 20px;
 `),O("&::after",`
 bottom: 0;
 left: 0;
 right: 0;
 height: 20px;
 `),C("shadow-start",[O("&::before",`
 box-shadow: inset 0 10px 8px -8px rgba(0, 0, 0, .12);
 `)]),C("shadow-end",[O("&::after",`
 box-shadow: inset 0 -10px 8px -8px rgba(0, 0, 0, .12);
 `)])])])])]),p("tabs-nav-scroll-wrapper",`
 flex: 1;
 position: relative;
 overflow: hidden;
 `,[p("tabs-nav-y-scroll",`
 height: 100%;
 width: 100%;
 overflow-y: auto; 
 scrollbar-width: none;
 `,[O("&::-webkit-scrollbar, &::-webkit-scrollbar-track-piece, &::-webkit-scrollbar-thumb",`
 width: 0;
 height: 0;
 display: none;
 `)]),O("&::before, &::after",`
 transition: box-shadow .3s var(--n-bezier);
 pointer-events: none;
 content: "";
 position: absolute;
 z-index: 1;
 `)]),p("tabs-nav-scroll-content",`
 display: flex;
 position: relative;
 min-width: 100%;
 min-height: 100%;
 width: fit-content;
 box-sizing: border-box;
 `),p("tabs-wrapper",`
 display: inline-flex;
 flex-wrap: nowrap;
 position: relative;
 `),p("tabs-tab-wrapper",`
 display: flex;
 flex-wrap: nowrap;
 flex-shrink: 0;
 flex-grow: 0;
 `),p("tabs-tab",`
 cursor: pointer;
 white-space: nowrap;
 flex-wrap: nowrap;
 display: inline-flex;
 align-items: center;
 color: var(--n-tab-text-color);
 font-size: var(--n-tab-font-size);
 background-clip: padding-box;
 padding: var(--n-tab-padding);
 transition:
 box-shadow .3s var(--n-bezier),
 color .3s var(--n-bezier),
 background-color .3s var(--n-bezier),
 border-color .3s var(--n-bezier);
 `,[C("disabled",{cursor:"not-allowed"}),U("close",`
 margin-left: 6px;
 transition:
 background-color .3s var(--n-bezier),
 color .3s var(--n-bezier);
 `),U("label",`
 display: flex;
 align-items: center;
 z-index: 1;
 `)]),p("tabs-bar",`
 position: absolute;
 bottom: 0;
 height: 2px;
 border-radius: 1px;
 background-color: var(--n-bar-color);
 transition:
 left .2s var(--n-bezier),
 max-width .2s var(--n-bezier),
 opacity .3s var(--n-bezier),
 background-color .3s var(--n-bezier);
 `,[O("&.transition-disabled",`
 transition: none;
 `),C("disabled",`
 background-color: var(--n-tab-text-color-disabled)
 `)]),p("tabs-pane-wrapper",`
 position: relative;
 overflow: hidden;
 transition: max-height .2s var(--n-bezier);
 `),p("tab-pane",`
 color: var(--n-pane-text-color);
 width: 100%;
 transition:
 color .3s var(--n-bezier),
 background-color .3s var(--n-bezier),
 opacity .2s var(--n-bezier);
 left: 0;
 right: 0;
 top: 0;
 `,[O("&.next-transition-leave-active, &.prev-transition-leave-active, &.next-transition-enter-active, &.prev-transition-enter-active",`
 transition:
 color .3s var(--n-bezier),
 background-color .3s var(--n-bezier),
 transform .2s var(--n-bezier),
 opacity .2s var(--n-bezier);
 `),O("&.next-transition-leave-active, &.prev-transition-leave-active",`
 position: absolute;
 `),O("&.next-transition-enter-from, &.prev-transition-leave-to",`
 transform: translateX(32px);
 opacity: 0;
 `),O("&.next-transition-leave-to, &.prev-transition-enter-from",`
 transform: translateX(-32px);
 opacity: 0;
 `),O("&.next-transition-leave-from, &.next-transition-enter-to, &.prev-transition-leave-from, &.prev-transition-enter-to",`
 transform: translateX(0);
 opacity: 1;
 `)]),p("tabs-tab-pad",`
 box-sizing: border-box;
 width: var(--n-tab-gap);
 flex-grow: 0;
 flex-shrink: 0;
 `),C("line-type, bar-type",[p("tabs-tab",`
 font-weight: var(--n-tab-font-weight);
 box-sizing: border-box;
 vertical-align: bottom;
 `,[O("&:hover",{color:"var(--n-tab-text-color-hover)"}),C("active",`
 color: var(--n-tab-text-color-active);
 font-weight: var(--n-tab-font-weight-active);
 `),C("disabled",{color:"var(--n-tab-text-color-disabled)"})])]),p("tabs-nav",[C("line-type",[C("top",[U("prefix, suffix",`
 border-bottom: 1px solid var(--n-tab-border-color);
 `),p("tabs-nav-scroll-content",`
 border-bottom: 1px solid var(--n-tab-border-color);
 `),p("tabs-bar",`
 bottom: -1px;
 `)]),C("left",[U("prefix, suffix",`
 border-right: 1px solid var(--n-tab-border-color);
 `),p("tabs-nav-scroll-content",`
 border-right: 1px solid var(--n-tab-border-color);
 `),p("tabs-bar",`
 right: -1px;
 `)]),C("right",[U("prefix, suffix",`
 border-left: 1px solid var(--n-tab-border-color);
 `),p("tabs-nav-scroll-content",`
 border-left: 1px solid var(--n-tab-border-color);
 `),p("tabs-bar",`
 left: -1px;
 `)]),C("bottom",[U("prefix, suffix",`
 border-top: 1px solid var(--n-tab-border-color);
 `),p("tabs-nav-scroll-content",`
 border-top: 1px solid var(--n-tab-border-color);
 `),p("tabs-bar",`
 top: -1px;
 `)]),U("prefix, suffix",`
 transition: border-color .3s var(--n-bezier);
 `),p("tabs-nav-scroll-content",`
 transition: border-color .3s var(--n-bezier);
 `),p("tabs-bar",`
 border-radius: 0;
 `)]),C("card-type",[U("prefix, suffix",`
 transition: border-color .3s var(--n-bezier);
 `),p("tabs-pad",`
 flex-grow: 1;
 transition: border-color .3s var(--n-bezier);
 `),p("tabs-tab-pad",`
 transition: border-color .3s var(--n-bezier);
 `),p("tabs-tab",`
 font-weight: var(--n-tab-font-weight);
 border: 1px solid var(--n-tab-border-color);
 background-color: var(--n-tab-color);
 box-sizing: border-box;
 position: relative;
 vertical-align: bottom;
 display: flex;
 justify-content: space-between;
 font-size: var(--n-tab-font-size);
 color: var(--n-tab-text-color);
 `,[C("addable",`
 padding-left: 8px;
 padding-right: 8px;
 font-size: 16px;
 justify-content: center;
 `,[U("height-placeholder",`
 width: 0;
 font-size: var(--n-tab-font-size);
 `),Va("disabled",[O("&:hover",`
 color: var(--n-tab-text-color-hover);
 `)])]),C("closable","padding-right: 8px;"),C("active",`
 background-color: #0000;
 font-weight: var(--n-tab-font-weight-active);
 color: var(--n-tab-text-color-active);
 `),C("disabled","color: var(--n-tab-text-color-disabled);")])]),C("left, right",`
 flex-direction: column; 
 `,[U("prefix, suffix",`
 padding: var(--n-tab-padding-vertical);
 `),p("tabs-wrapper",`
 flex-direction: column;
 `),p("tabs-tab-wrapper",`
 flex-direction: column;
 `,[p("tabs-tab-pad",`
 height: var(--n-tab-gap-vertical);
 width: 100%;
 `)])]),C("top",[C("card-type",[p("tabs-scroll-padding","border-bottom: 1px solid var(--n-tab-border-color);"),U("prefix, suffix",`
 border-bottom: 1px solid var(--n-tab-border-color);
 `),p("tabs-tab",`
 border-top-left-radius: var(--n-tab-border-radius);
 border-top-right-radius: var(--n-tab-border-radius);
 `,[C("active",`
 border-bottom: 1px solid #0000;
 `)]),p("tabs-tab-pad",`
 border-bottom: 1px solid var(--n-tab-border-color);
 `),p("tabs-pad",`
 border-bottom: 1px solid var(--n-tab-border-color);
 `)])]),C("left",[C("card-type",[p("tabs-scroll-padding","border-right: 1px solid var(--n-tab-border-color);"),U("prefix, suffix",`
 border-right: 1px solid var(--n-tab-border-color);
 `),p("tabs-tab",`
 border-top-left-radius: var(--n-tab-border-radius);
 border-bottom-left-radius: var(--n-tab-border-radius);
 `,[C("active",`
 border-right: 1px solid #0000;
 `)]),p("tabs-tab-pad",`
 border-right: 1px solid var(--n-tab-border-color);
 `),p("tabs-pad",`
 border-right: 1px solid var(--n-tab-border-color);
 `)])]),C("right",[C("card-type",[p("tabs-scroll-padding","border-left: 1px solid var(--n-tab-border-color);"),U("prefix, suffix",`
 border-left: 1px solid var(--n-tab-border-color);
 `),p("tabs-tab",`
 border-top-right-radius: var(--n-tab-border-radius);
 border-bottom-right-radius: var(--n-tab-border-radius);
 `,[C("active",`
 border-left: 1px solid #0000;
 `)]),p("tabs-tab-pad",`
 border-left: 1px solid var(--n-tab-border-color);
 `),p("tabs-pad",`
 border-left: 1px solid var(--n-tab-border-color);
 `)])]),C("bottom",[C("card-type",[p("tabs-scroll-padding","border-top: 1px solid var(--n-tab-border-color);"),U("prefix, suffix",`
 border-top: 1px solid var(--n-tab-border-color);
 `),p("tabs-tab",`
 border-bottom-left-radius: var(--n-tab-border-radius);
 border-bottom-right-radius: var(--n-tab-border-radius);
 `,[C("active",`
 border-top: 1px solid #0000;
 `)]),p("tabs-tab-pad",`
 border-top: 1px solid var(--n-tab-border-color);
 `),p("tabs-pad",`
 border-top: 1px solid var(--n-tab-border-color);
 `)])])])]),Bt=Kn,Nn=Object.assign(Object.assign({},da.props),{value:[String,Number],defaultValue:[String,Number],trigger:{type:String,default:"click"},type:{type:String,default:"bar"},closable:Boolean,justifyContent:String,size:String,placement:{type:String,default:"top"},tabStyle:[String,Object],tabClass:String,addTabStyle:[String,Object],addTabClass:String,barWidth:Number,paneClass:String,paneStyle:[String,Object],paneWrapperClass:String,paneWrapperStyle:[String,Object],addable:[Boolean,Object],tabsPadding:{type:Number,default:0},animated:Boolean,onBeforeLeave:Function,onAdd:Function,"onUpdate:value":[Function,Array],onUpdateValue:[Function,Array],onClose:[Function,Array],labelSize:String,activeName:[String,Number],onActiveNameChange:[Function,Array]}),Mn=pt({name:"Tabs",props:Nn,slots:Object,setup(e,{slots:n}){var r,d,o,f;const{mergedClsPrefixRef:g,inlineThemeDisabled:k,mergedComponentPropsRef:x}=Ga(e),P=da("Tabs","-tabs",Bn,Xa,e,g),z=h(null),$=h(null),j=h(null),M=h(null),B=h(null),K=h(null),q=h(!0),W=h(!0),E=Zt(e,["labelSize","size"]),ae=S(()=>{var u,l;if(E.value)return E.value;const b=(l=(u=x?.value)===null||u===void 0?void 0:u.Tabs)===null||l===void 0?void 0:l.size;return b||"medium"}),V=Zt(e,["activeName","value"]),T=h((d=(r=V.value)!==null&&r!==void 0?r:e.defaultValue)!==null&&d!==void 0?d:n.default?(f=(o=$t(n.default())[0])===null||o===void 0?void 0:o.props)===null||f===void 0?void 0:f.name:null),_=hn(V,T),F={id:0},G=S(()=>{if(!(!e.justifyContent||e.type==="card"))return{display:"flex",justifyContent:e.justifyContent}});Ve(_,()=>{F.id=0,ne(),Xe()});function ve(){var u;const{value:l}=_;return l===null?null:(u=z.value)===null||u===void 0?void 0:u.querySelector(`[data-name="${l}"]`)}function vt(u){if(e.type==="card")return;const{value:l}=$;if(!l)return;const b=l.style.opacity==="0";if(u){const w=`${g.value}-tabs-bar--disabled`,{barWidth:I,placement:Q}=e;if(u.dataset.disabled==="true"?l.classList.add(w):l.classList.remove(w),["top","bottom"].includes(Q)){if(Ge(["top","maxHeight","height"]),typeof I=="number"&&u.offsetWidth>=I){const H=Math.floor((u.offsetWidth-I)/2)+u.offsetLeft;l.style.left=`${H}px`,l.style.maxWidth=`${I}px`}else l.style.left=`${u.offsetLeft}px`,l.style.maxWidth=`${u.offsetWidth}px`;l.style.width="8192px",b&&(l.style.transition="none"),l.offsetWidth,b&&(l.style.transition="",l.style.opacity="1")}else{if(Ge(["left","maxWidth","width"]),typeof I=="number"&&u.offsetHeight>=I){const H=Math.floor((u.offsetHeight-I)/2)+u.offsetTop;l.style.top=`${H}px`,l.style.maxHeight=`${I}px`}else l.style.top=`${u.offsetTop}px`,l.style.maxHeight=`${u.offsetHeight}px`;l.style.height="8192px",b&&(l.style.transition="none"),l.offsetHeight,b&&(l.style.transition="",l.style.opacity="1")}}}function be(){if(e.type==="card")return;const{value:u}=$;u&&(u.style.opacity="0")}function Ge(u){const{value:l}=$;if(l)for(const b of u)l.style[b]=""}function ne(){if(e.type==="card")return;const u=ve();u?vt(u):be()}function Xe(){var u;const l=(u=B.value)===null||u===void 0?void 0:u.$el;if(!l)return;const b=ve();if(!b)return;const{scrollLeft:w,offsetWidth:I}=l,{offsetLeft:Q,offsetWidth:H}=b;w>Q?l.scrollTo({top:0,left:Q,behavior:"smooth"}):Q+H>w+I&&l.scrollTo({top:0,left:Q+H-I,behavior:"smooth"})}const Pe=h(null);let Re=0,X=null;function Y(u){const l=Pe.value;if(l){Re=u.getBoundingClientRect().height;const b=`${Re}px`,w=()=>{l.style.height=b,l.style.maxHeight=b};X?(w(),X(),X=null):X=w}}function R(u){const l=Pe.value;if(l){const b=u.getBoundingClientRect().height,w=()=>{document.body.offsetHeight,l.style.maxHeight=`${b}px`,l.style.height=`${Math.max(Re,b)}px`};X?(X(),X=null,w()):X=w}}function se(){const u=Pe.value;if(u){u.style.maxHeight="",u.style.height="";const{paneWrapperStyle:l}=e;if(typeof l=="string")u.style.cssText=l;else if(l){const{maxHeight:b,height:w}=l;b!==void 0&&(u.style.maxHeight=b),w!==void 0&&(u.style.height=w)}}}const ze={value:[]},We=h("next");function Ie(u){const l=_.value;let b="next";for(const w of ze.value){if(w===l)break;if(w===u){b="prev";break}}We.value=b,ke(u)}function ke(u){const{onActiveNameChange:l,onUpdateValue:b,"onUpdate:value":w}=e;l&&ot(l,u),b&&ot(b,u),w&&ot(w,u),T.value=u}function Fe(u){const{onClose:l}=e;l&&ot(l,u)}function Ae(){const{value:u}=$;if(!u)return;const l="transition-disabled";u.classList.add(l),ne(),u.classList.remove(l)}const ie=h(null);function ge({transitionDisabled:u}){const l=z.value;if(!l)return;u&&l.classList.add("transition-disabled");const b=ve();b&&ie.value&&(ie.value.style.width=`${b.offsetWidth}px`,ie.value.style.height=`${b.offsetHeight}px`,ie.value.style.transform=`translateX(${b.offsetLeft-kn(getComputedStyle(l).paddingLeft)}px)`,u&&ie.value.offsetWidth),u&&l.classList.remove("transition-disabled")}Ve([_],()=>{e.type==="segment"&&At(()=>{ge({transitionDisabled:!1})})}),ua(()=>{e.type==="segment"&&ge({transitionDisabled:!0})});let Z=0;function Qe(u){var l;if(u.contentRect.width===0&&u.contentRect.height===0||Z===u.contentRect.width)return;Z=u.contentRect.width;const{type:b}=e;if((b==="line"||b==="bar")&&Ae(),b!=="segment"){const{placement:w}=e;je((w==="top"||w==="bottom"?(l=B.value)===null||l===void 0?void 0:l.$el:K.value)||null)}}const $e=Bt(Qe,64);Ve([()=>e.justifyContent,()=>e.size],()=>{At(()=>{const{type:u}=e;(u==="line"||u==="bar")&&Ae()})});const ye=h(!1);function bt(u){var l;const{target:b,contentRect:{width:w,height:I}}=u,Q=b.parentElement.parentElement.offsetWidth,H=b.parentElement.parentElement.offsetHeight,{placement:le}=e;if(!ye.value)le==="top"||le==="bottom"?Q<w&&(ye.value=!0):H<I&&(ye.value=!0);else{const{value:ue}=M;if(!ue)return;le==="top"||le==="bottom"?Q-w>ue.$el.offsetWidth&&(ye.value=!1):H-I>ue.$el.offsetHeight&&(ye.value=!1)}je(((l=B.value)===null||l===void 0?void 0:l.$el)||null)}const mt=Bt(bt,64);function gt(){const{onAdd:u}=e;u&&u(),At(()=>{const l=ve(),{value:b}=B;!l||!b||b.scrollTo({left:l.offsetLeft,top:0,behavior:"smooth"})})}function je(u){if(!u)return;const{placement:l}=e;if(l==="top"||l==="bottom"){const{scrollLeft:b,scrollWidth:w,offsetWidth:I}=u;q.value=b<=0,W.value=b+I>=w}else{const{scrollTop:b,scrollHeight:w,offsetHeight:I}=u;q.value=b<=0,W.value=b+I>=w}}const Le=Bt(u=>{je(u.target)},64);Oa(qt,{triggerRef:ce(e,"trigger"),tabStyleRef:ce(e,"tabStyle"),tabClassRef:ce(e,"tabClass"),addTabStyleRef:ce(e,"addTabStyle"),addTabClassRef:ce(e,"addTabClass"),paneClassRef:ce(e,"paneClass"),paneStyleRef:ce(e,"paneStyle"),mergedClsPrefixRef:g,typeRef:ce(e,"type"),closableRef:ce(e,"closable"),valueRef:_,tabChangeIdRef:F,onBeforeLeaveRef:ce(e,"onBeforeLeave"),activateTab:Ie,handleClose:Fe,handleAdd:gt}),xn(()=>{ne(),Xe()}),Aa(()=>{const{value:u}=j;if(!u)return;const{value:l}=g,b=`${l}-tabs-nav-scroll-wrapper--shadow-start`,w=`${l}-tabs-nav-scroll-wrapper--shadow-end`;q.value?u.classList.remove(b):u.classList.add(b),W.value?u.classList.remove(w):u.classList.add(w)});const qe={syncBarPosition:()=>{ne()}},Ee=()=>{ge({transitionDisabled:!0})},_e=S(()=>{const{value:u}=ae,{type:l}=e,b={card:"Card",bar:"Bar",line:"Line",segment:"Segment"}[l],w=`${u}${b}`,{self:{barColor:I,closeIconColor:Q,closeIconColorHover:H,closeIconColorPressed:le,tabColor:ue,tabBorderColor:Je,paneTextColor:yt,tabFontWeight:Ye,tabBorderRadius:ht,tabFontWeightActive:xt,colorSegment:de,fontWeightStrong:Ze,tabColorSegment:et,closeSize:kt,closeIconSize:_t,closeColorHover:Ue,closeColorPressed:Ct,closeBorderRadius:Ce,[fe("panePadding",u)]:Ke,[fe("tabPadding",w)]:wt,[fe("tabPaddingVertical",w)]:St,[fe("tabGap",w)]:tt,[fe("tabGap",`${w}Vertical`)]:He,[fe("tabTextColor",l)]:Tt,[fe("tabTextColorActive",l)]:Pt,[fe("tabTextColorHover",l)]:at,[fe("tabTextColorDisabled",l)]:Rt,[fe("tabFontSize",u)]:nt},common:{cubicBezierEaseInOut:zt}}=P.value;return{"--n-bezier":zt,"--n-color-segment":de,"--n-bar-color":I,"--n-tab-font-size":nt,"--n-tab-text-color":Tt,"--n-tab-text-color-active":Pt,"--n-tab-text-color-disabled":Rt,"--n-tab-text-color-hover":at,"--n-pane-text-color":yt,"--n-tab-border-color":Je,"--n-tab-border-radius":ht,"--n-close-size":kt,"--n-close-icon-size":_t,"--n-close-color-hover":Ue,"--n-close-color-pressed":Ct,"--n-close-border-radius":Ce,"--n-close-icon-color":Q,"--n-close-icon-color-hover":H,"--n-close-icon-color-pressed":le,"--n-tab-color":ue,"--n-tab-font-weight":Ye,"--n-tab-font-weight-active":xt,"--n-tab-padding":wt,"--n-tab-padding-vertical":St,"--n-tab-gap":tt,"--n-tab-gap-vertical":He,"--n-pane-padding-left":ct(Ke,"left"),"--n-pane-padding-right":ct(Ke,"right"),"--n-pane-padding-top":ct(Ke,"top"),"--n-pane-padding-bottom":ct(Ke,"bottom"),"--n-font-weight-strong":Ze,"--n-tab-color-segment":et}}),oe=k?Qa("tabs",S(()=>`${ae.value[0]}${e.type[0]}`),_e,e):void 0;return Object.assign({mergedClsPrefix:g,mergedValue:_,renderedNames:new Set,segmentCapsuleElRef:ie,tabsPaneWrapperRef:Pe,tabsElRef:z,barElRef:$,addTabInstRef:M,xScrollInstRef:B,scrollWrapperElRef:j,addTabFixed:ye,tabWrapperStyle:G,handleNavResize:$e,mergedSize:ae,handleScroll:Le,handleTabsResize:mt,cssVars:k?void 0:_e,themeClass:oe?.themeClass,animationDirection:We,renderNameListRef:ze,yScrollElRef:K,handleSegmentResize:Ee,onAnimationBeforeLeave:Y,onAnimationEnter:R,onAnimationAfterEnter:se,onRender:oe?.onRender},qe)},render(){const{mergedClsPrefix:e,type:n,placement:r,addTabFixed:d,addable:o,mergedSize:f,renderNameListRef:g,onRender:k,paneWrapperClass:x,paneWrapperStyle:P,$slots:{default:z,prefix:$,suffix:j}}=this;k?.();const M=z?$t(z()).filter(T=>T.type.__TAB_PANE__===!0):[],B=z?$t(z()).filter(T=>T.type.__TAB__===!0):[],K=!B.length,q=n==="card",W=n==="segment",E=!q&&!W&&this.justifyContent;g.value=[];const ae=()=>{const T=v("div",{style:this.tabWrapperStyle,class:`${e}-tabs-wrapper`},E?null:v("div",{class:`${e}-tabs-scroll-padding`,style:r==="top"||r==="bottom"?{width:`${this.tabsPadding}px`}:{height:`${this.tabsPadding}px`}}),K?M.map((_,F)=>(g.value.push(_.props.name),Nt(v(Ft,Object.assign({},_.props,{internalCreatedByPane:!0,internalLeftPadded:F!==0&&(!E||E==="center"||E==="start"||E==="end")}),_.children?{default:_.children.tab}:void 0)))):B.map((_,F)=>(g.value.push(_.props.name),Nt(F!==0&&!E?oa(_):_))),!d&&o&&q?ra(o,(K?M.length:B.length)!==0):null,E?null:v("div",{class:`${e}-tabs-scroll-padding`,style:{width:`${this.tabsPadding}px`}}));return v("div",{ref:"tabsElRef",class:`${e}-tabs-nav-scroll-content`},q&&o?v(Kt,{onResize:this.handleTabsResize},{default:()=>T}):T,q?v("div",{class:`${e}-tabs-pad`}):null,q?null:v("div",{ref:"barElRef",class:`${e}-tabs-bar`}))},V=W?"top":r;return v("div",{class:[`${e}-tabs`,this.themeClass,`${e}-tabs--${n}-type`,`${e}-tabs--${f}-size`,E&&`${e}-tabs--flex`,`${e}-tabs--${V}`],style:this.cssVars},v("div",{class:[`${e}-tabs-nav--${n}-type`,`${e}-tabs-nav--${V}`,`${e}-tabs-nav`]},Dt($,T=>T&&v("div",{class:`${e}-tabs-nav__prefix`},T)),W?v(Kt,{onResize:this.handleSegmentResize},{default:()=>v("div",{class:`${e}-tabs-rail`,ref:"tabsElRef"},v("div",{class:`${e}-tabs-capsule`,ref:"segmentCapsuleElRef"},v("div",{class:`${e}-tabs-wrapper`},v("div",{class:`${e}-tabs-tab`}))),K?M.map((T,_)=>(g.value.push(T.props.name),v(Ft,Object.assign({},T.props,{internalCreatedByPane:!0,internalLeftPadded:_!==0}),T.children?{default:T.children.tab}:void 0))):B.map((T,_)=>(g.value.push(T.props.name),_===0?T:oa(T))))}):v(Kt,{onResize:this.handleNavResize},{default:()=>v("div",{class:`${e}-tabs-nav-scroll-wrapper`,ref:"scrollWrapperElRef"},["top","bottom"].includes(V)?v(yn,{ref:"xScrollInstRef",onScroll:this.handleScroll},{default:ae}):v("div",{class:`${e}-tabs-nav-y-scroll`,onScroll:this.handleScroll,ref:"yScrollElRef"},ae()))}),d&&o&&q?ra(o,!0):null,Dt(j,T=>T&&v("div",{class:`${e}-tabs-nav__suffix`},T))),K&&(this.animated&&(V==="top"||V==="bottom")?v("div",{ref:"tabsPaneWrapperRef",style:P,class:[`${e}-tabs-pane-wrapper`,x]},na(M,this.mergedValue,this.renderedNames,this.onAnimationBeforeLeave,this.onAnimationEnter,this.onAnimationAfterEnter,this.animationDirection)):na(M,this.mergedValue,this.renderedNames)))}});function na(e,n,r,d,o,f,g){const k=[];return e.forEach(x=>{const{name:P,displayDirective:z,"display-directive":$}=x.props,j=B=>z===B||$===B,M=n===P;if(x.key!==void 0&&(x.key=P),M||j("show")||j("show:lazy")&&r.has(P)){r.has(P)||r.add(P);const B=!j("if");k.push(B?$a(x,[[La,M]]):x)}}),g?v(Ea,{name:`${g}-transition`,onBeforeLeave:d,onEnter:o,onAfterEnter:f},{default:()=>k}):k}function ra(e,n){return v(Ft,{ref:"addTabInstRef",key:"__addable",name:"__addable",internalCreatedByPane:!0,internalAddable:!0,internalLeftPadded:n,disabled:typeof e=="object"&&e.disabled})}function oa(e){const n=Ka(e);return n.props?n.props.internalLeftPadded=!0:n.props={internalLeftPadded:!0},n}function Nt(e){return Array.isArray(e.dynamicProps)?e.dynamicProps.includes("internalLeftPadded")||e.dynamicProps.push("internalLeftPadded"):e.dynamicProps=["internalLeftPadded"],e}function Wn(e){return te("GET","/api/keys",{signal:e})}function Fn(e){return te("POST","/api/keys",{body:e})}function jn(e){return te("GET","/api/keys/"+e+"/view")}function qn(e){return te("GET","/api/keys/"+e+"/usage")}function Un(e,n={}){return te("GET","/api/keys/"+e+"/ips",{query:{days:n.days??1,sort:n.sort??"requests",direction:n.direction??"desc",limit:n.limit??100,offset:n.offset??0}})}function Mt(e,n){return te("PATCH","/api/keys/"+e,{body:n})}function Hn(e){return te("DELETE","/api/keys/"+e)}function Dn(){return te("GET","/api/credits/selection-policy")}function Vn(){return te("GET","/api/app/network")}function Gn(){return te("GET","/api/models/aggregate")}function Xn(){return te("GET","/api/groups")}function Qn(e={}){return te("GET","/api/accounts",{query:{all_platforms:1,page:e.page??1,page_size:e.pageSize??50,search:e.search||""}})}function Jn(){return te("GET","/api/platforms")}function Yn(e){return(Math.round(e*1e3)/10).toFixed(1)+"%"}function Zn(e){const n=!!e.unlimited,r=Number(e.credit_limit||0),d=Number(e.credit_used||0),o=n||r<=0?0:Math.min(1,Math.max(0,d/r)),f=n?"ok":o>=.9?"danger":o>=.6?"warn":"ok",g=e.ip_summary||{days:1,unique_ips:0,requests:0,active_ips:0,active_requests:0},k=e.max_concurrent??null,x=e.rate_per_minute??null,P=Array.isArray(e.account_ids)?e.account_ids.map(Number).filter($=>$>0):[],z=e.masked||(e.key_prefix?bn(e.key_prefix):Me);return{id:e.id,name:e.name||"未命名",masked:z,keyPrefix:e.key_prefix||"",limitText:n?"∞":Te(r),usedText:Te(d),usageRatio:o,usageTone:f,usageText:n?"不限额度":Yn(o),unlimited:n,concurrencyText:k===0?"不限":k==null?"全局":String(k),rateText:x===0?"不限频率":x==null?"频率随全局":x+" 次/分",ipText:(Number(g.unique_ips)||0)+" 个 / 24h",ipBadges:(g.top_ips||[]).slice(0,3).map($=>({ip:$.ip,text:$.ip+" · "+(Number($.active_requests)||0)+" 并发 · "+(Number($.requests)||0)+" 次"})),groupLabel:e.group_name||"全部模型",statusLabel:e.status==="active"?"正常":"已停用",statusTone:e.status==="active"?"ok":"neutral",accountLabel:P.length?"独占 "+P.length+" 个账号":e.account_id?"独占账号："+(e.account_name||"#"+e.account_id):"公共账号池",expireText:Me,note:e.note||"",platform:e.platform||"",aggregate:!!e.aggregate,groupId:Number(e.group_id||0),createdAt:e.created_at||"",status:e.status,creditLimit:r,creditUsed:d,maxConcurrent:k,ratePerMinute:x,accountIds:P,accountId:e.account_id,accountName:e.account_name||"",ipSummary:g}}function er(e){return Array.isArray(e)?e.map(Zn):[]}function tr(e,n){const r=String(n.q||"").trim().toLowerCase(),d=String(n.group||""),o=String(n.status||"");return e.filter(f=>!(r&&![f.name,f.note,f.accountName,f.platform].join(" ").toLowerCase().includes(r)||d==="none"&&f.groupId!==0||d&&d!=="none"&&String(f.groupId)!==d||o&&f.status!==o))}function ar(e,n,r){if(!n||r!=="asc"&&r!=="desc")return e;const d=r==="asc"?1:-1,o=f=>n==="name"?0:n==="limit"?f.unlimited?Number.MAX_SAFE_INTEGER:f.creditLimit:n==="used"?f.creditUsed:n==="concurrency"?f.maxConcurrent==null?-1:f.maxConcurrent:n==="created"&&Date.parse(f.createdAt||"")||0;return[...e].sort((f,g)=>n==="name"?f.name.localeCompare(g.name,"zh-CN")*d:(o(f)-o(g))*d)}function nr(e,n,r){const d=Math.max(1,Math.floor(r||20)),o=e.length,f=Math.max(1,Math.ceil(o/d)),g=Math.min(Math.max(1,Math.floor(n||1)),f);return{items:e.slice((g-1)*d,g*d),total:o,page:g,pageCount:f}}function sa(e){return{total:e.length,active:e.filter(n=>n.status==="active").length,revoked:e.filter(n=>n.status!=="active").length,unlimited:e.filter(n=>n.unlimited).length}}function rr(e){return{status:e==="active"?"revoked":"active"}}function jt(e,n){const r=String(e||""),d=String(n||"");return!r||!d||r.toLowerCase().endsWith("-"+d.toLowerCase())?r:r+"-"+d}function or(e,n=12){const r=(e?.items||[]).filter(o=>(o.platforms||[]).some(f=>f.enabled!==!1)),d=r.slice(0,Math.max(0,n)).map(o=>{const f=(o.platforms||[]).filter(x=>x.enabled!==!1),g=f[0],k=(f.length?f.map(x=>{const P=x.client_id||jt(x.model_id,x.platform),z=x.multiplier==null?Me:Number(x.multiplier)===0?"FREE":"×"+x.multiplier;return(x.platform_label||x.platform)+" · "+P+" · "+z}).join(`
`):"暂无平台明细")+`
点击复制调用名（带平台后缀）`;return{key:o.key||String(o.name),name:o.name,platformCount:f.length||Number(o.platform_count||0),copyValue:g?g.client_id||jt(g.model_id,g.platform):"",title:k}});return{chips:d,total:r.length,rest:Math.max(0,r.length-d.length)}}function sr(e,n){const r=String(n||"").trim().toLowerCase();return(e?.items||[]).filter(d=>(d.platforms||[]).some(o=>o.enabled!==!1)).map(d=>{const o=(d.platforms||[]).filter(f=>f.enabled!==!1);return{key:d.key||String(d.name),name:d.name,platformCount:o.length,rows:o.map(f=>({platform:f.platform,platformLabel:f.platform_label||f.platform,callId:f.client_id||jt(f.model_id,f.platform),upstreamId:f.model_id,multiplierText:f.multiplier==null?Me:Number(f.multiplier)===0?"FREE":"×"+f.multiplier}))}}).filter(d=>!r||d.name.toLowerCase().includes(r)?!0:d.rows.some(o=>o.callId.toLowerCase().includes(r)||o.upstreamId.toLowerCase().includes(r)))}function ir(e,n){const r=new Map;n.origin&&r.set(n.origin,"当前访问 · "+(n.origin.replace(/^https?:\/\//,"")||"当前域名"));const d=["localhost","127.0.0.1"].includes(n.hostname),o=n.port||(d&&e?.port?String(e.port):""),f=o?":"+o:"";r.set("http://127.0.0.1"+f,"本机 · 127.0.0.1"+f);for(const g of e?.lan_ips||[])r.set("http://"+g+f,"局域网 · "+g+f);return[...r].map(([g,k])=>({value:g,label:k}))}function lr(e,n){const r=(e||"").replace(/\/+$/,"")+"/v1",d=(e||"").replace(/\/+$/,""),o=n||"auto";return{openai:["curl "+r+"/chat/completions \\",'  -H "Authorization: Bearer <你的 Key>" \\','  -H "Content-Type: application/json" \\',`  -d '{"model":"`+o+`","messages":[{"role":"user","content":"你好"}]}'`].join(`
`),claude:["curl "+d+"/v1/messages \\",'  -H "x-api-key: <你的 Key>" \\','  -H "anthropic-version: 2023-06-01" \\','  -H "Content-Type: application/json" \\',`  -d '{"model":"claude-sonnet-4-5-20250929","max_tokens":1024,"messages":[{"role":"user","content":"你好"}]}'`].join(`
`),audioCurl:['curl "'+r+'/audio/transcriptions" \\','  -H "Authorization: Bearer <你的 Key>" \\','  -F "file=@recording.webm" \\','  -F "model=workbuddy-asr" \\','  -F "response_format=json"'].join(`
`),audioPython:["from openai import OpenAI",'client = OpenAI(api_key="<你的 Key>", base_url="'+r+'")','with open("recording.webm", "rb") as audio:','    result = client.audio.transcriptions.create(model="workbuddy-asr", file=audio)',"print(result.text)"].join(`
`),imagesPython:["from openai import OpenAI",'client = OpenAI(api_key="<你的 Key>", base_url="'+r+'")','result = client.images.generate(model="hunyuan-image-alpha", prompt="白色背景上的蓝色圆形", n=1)',"print(result.data[0].url)",'with open("input.png", "rb") as image:','    edited = client.images.edit(model="hunyuan-image-alpha", image=image, prompt="把圆形改成绿色")',"print(edited.data[0].url)"].join(`
`),videoCurl:["# 1) 创建任务：立即返回 queued，不阻塞等待生成",'curl "'+r+'/videos" \\','  -H "Authorization: Bearer <你的 Key>" \\','  -H "Content-Type: application/json" \\',`  -d '{"model":"kling-video-v3","prompt":"一只猫在弹钢琴","seconds":"4","size":"1280x720"}'`,"","# 2) 轮询（用上一步返回的 id），直到 status=completed",'curl "'+r+'/videos/<视频 ID>" -H "Authorization: Bearer <你的 Key>"',"","# 3) 下载 MP4",'curl "'+r+'/videos/<视频 ID>/content" -H "Authorization: Bearer <你的 Key>" -o output.mp4'].join(`
`),videoPython:["import time","from openai import OpenAI",'client = OpenAI(api_key="<你的 Key>", base_url="'+r+'")',"",'video = client.videos.create(model="kling-video-v3", prompt="一只猫在弹钢琴", seconds="4", size="1280x720")',"print(video.id, video.status)          # queued","",'while video.status not in ("completed", "failed"):',"    time.sleep(5)","    video = client.videos.retrieve(video.id)","",'if video.status == "completed":',"    content = client.videos.download_content(video.id)",'    content.write_to_file("output.mp4")'].join(`
`)}}function ia(e,n){const r=String(e||"").trim();if(!r)return;const d=Number(r);if(!Number.isFinite(d))return n+"必须是数字";if(d<0)return n+"不能小于 0";if(!Number.isInteger(d))return n+"必须是整数"}function ur(e,n){const r={};if(n&&!String(e.name||"").trim()&&(r.name="名称不能为空"),!e.unlimited){const d=String(e.creditLimit||"").trim();if(d){const o=Number(d);Number.isFinite(o)?o<0&&(r.creditLimit="额度不能小于 0"):r.creditLimit="额度必须是数字"}}return r.maxConcurrent=ia(e.maxConcurrent,"并发上限"),r.ratePerMinute=ia(e.ratePerMinute,"每分钟请求数"),r}function dr(e){return Object.values(e).some(Boolean)}function cr(e){const n=d=>{const o=String(d||"").trim();return o===""?null:Number(o)},r=String(e.creditLimit||"").trim();return{name:String(e.name||"").trim(),note:String(e.note||""),group_id:Number(e.groupId||0),unlimited:e.unlimited||r==="",credit_limit:r===""?null:Number(r),max_concurrent:n(e.maxConcurrent),rate_per_minute:n(e.ratePerMinute),aggregate:!!e.aggregate,platform:String(e.platform||"").toLowerCase(),account_ids:e.accountIds.slice()}}function fr(e){const n=!!e.unlimited,r=e.usage_percent,d=n||r==null?0:Math.min(1,Math.max(0,r/100));return{limitText:n?"∞（无限制）":Te(e.credit_limit),usedText:Te(e.credit_used),remainingText:n||e.credit_remaining==null?"∞":Te(e.credit_remaining),percentText:r==null?"N/A":r.toFixed(1)+"%",ratio:d,tone:n?"ok":d>=.9?"danger":d>=.6?"warn":"ok",unlimited:n,createdText:e.created_at?ca(e.created_at):Me}}function pa(e){return{totalIps:Number(e.total_ips||0),requests:Number(e.total_requests||0),activeIps:Number(e.active_ips||0),activeRequests:Number(e.active_requests||0),rows:(e.items||[]).map(n=>({ip:n.ip,requests:Number(n.requests||0),failed:Number(n.failed||0),credits:Number(n.credits||0),tokens:Number(n.tokens||0),lastSeen:n.last_seen?ca(n.last_seen):Me,models:(n.models||[]).map(r=>r.model).filter(Boolean).join("、"),active:Number(n.active_requests||0),blocked:!!n.blocked}))}}function Wt(){return pa({total_ips:0,total_requests:0,active_ips:0,active_requests:0,items:[]})}const pr={class:"keys-page"},vr={class:"keys-policy"},br={class:"keys-share__grid"},mr={class:"keys-share__row"},gr={class:"keys-share__row"},yr={class:"keys-share__row"},hr={class:"keys-share__row"},xr={class:"keys-share__row keys-share__row--models"},kr={class:"keys-share__models"},_r={class:"keys-muted"},Cr={key:0,class:"keys-chip-count"},wr={key:1,class:"keys-muted"},Sr={class:"keys-filter"},Tr={class:"keys-body"},Pr={key:1,class:"keys-skeleton","aria-busy":"true","aria-label":"Key 加载中"},Rr={class:"keys-form"},zr={class:"keys-form"},Ir={class:"keys-form__inline"},Ar={class:"keys-form"},$r={class:"keys-modal__footer"},Lr={key:1,class:"keys-skeleton"},Er={class:"keys-usage-grid"},Kr={class:"keys-usage-cell"},Or={class:"tnum"},Br={class:"keys-usage-cell"},Nr={class:"tnum"},Mr={class:"keys-usage-cell"},Wr={class:"tnum"},Fr={class:"keys-usage-cell"},jr={class:"tnum"},qr={class:"keys-hint"},Ur={class:"keys-ipbar"},Hr={key:0,class:"keys-muted"},Dr={class:"keys-model__head"},Vr={class:"keys-muted"},Gr={class:"keys-model__rows"},Xr={class:"keys-muted"},Qr={class:"keys-muted tnum"},Jr="对外共享的调用凭证：每个 Key 单独计量、限额与封禁，共享出去不影响后台登录。OpenAI 与 Anthropic 两套协议共用同一批 Key、同一套配额与用量记账，只是 base_url 约定不同：OpenAI SDK 填含 /v1 的那个，Claude Code / Anthropic SDK 填不含 /v1 的那个（SDK 会自己拼 /v1/messages，填错会变成 /v1/v1/messages）。",Yr="OpenAI 与 Anthropic 两套协议共用同一批 Key、同一套配额与用量记账，只是 base_url 约定不同：OpenAI SDK 填 OpenAI 地址（含 /v1），Claude Code / Anthropic SDK 填 Claude 地址（不含 /v1，SDK 会自己拼 /v1/messages，填错会变成 /v1/v1/messages）。Claude 端的 claude-sonnet-4-5 这类模型名由服务端自动按档次映射到白名单里的模型，无需手动改。",Zr=pt({__name:"KeysPage",setup(e){const n=Na(),r=tn(),d=pn(),o=vn("keys",{pageSize:20,filters:{q:"",group:"",status:""}}),f=h(null),g=h(!1),k=h(null);let x=null;const P=S(()=>er(f.value)),z=S(()=>tr(P.value,o.state.filters)),$=S(()=>ar(z.value,o.state.sortBy,o.state.sortOrder)),j=S(()=>nr($.value,o.state.page,o.state.pageSize)),M=S(()=>sa(P.value)),B=S(()=>g.value&&f.value===null),K=S(()=>!!(o.state.filters.q||o.state.filters.group||o.state.filters.status)),q=S(()=>{const a=sa(z.value);return K.value?"筛选出 "+a.total+" / 共 "+M.value.total+" 个 Key · 正常 "+a.active:"共 "+a.total+" 个 Key · 正常 "+a.active+" · 已停用 "+a.revoked+" · 不限额 "+a.unlimited}),W=S(()=>K.value?"没有符合条件的 Key。清空筛选后可看到全部 Key。":"还没有 Key。创建 Key 后即可对外调用网关。"),E=h(null),ae=h(null),V=h(null),T=h(!1),_={origin:typeof window<"u"?window.location.origin:"",hostname:typeof window<"u"?window.location.hostname:"",port:typeof window<"u"?window.location.port:""},F=S(()=>ir(ae.value,_)),G=h(_.origin);Ve(F,a=>{a.length&&!a.some(t=>t.value===G.value)&&(G.value=a[0].value)});const ve=S(()=>(G.value||"").replace(/\/+$/,"")+"/v1"),vt=S(()=>(G.value||"").replace(/\/+$/,"")),be=S(()=>or(V.value,12)),Ge=S(()=>be.value.chips[0]?.copyValue||""),ne=S(()=>lr(G.value,Ge.value)),Xe=S(()=>(E.value?.modes||[]).find(t=>t.value===E.value?.mode)?.label||"账号调度策略"),Pe=S(()=>((E.value?.modes||[]).find(t=>t.value===E.value?.mode)?.description||"决定网关挑哪一个账号承接请求。")+"（在「积分到期」页修改）");function Re(){return{name:"",note:"",groupId:0,unlimited:!0,creditLimit:"",maxConcurrent:"",ratePerMinute:"",aggregate:!0,platform:"",accountIds:[],resetUsed:!1}}const X=h(!1),Y=h(null),R=h(Re()),se=h({}),ze=h(!1),We=h(""),Ie=h(!1),ke=h([]),Fe=h([]),Ae=h([]),ie=h(!1),ge=h(!1),Z=h({groups:!1,platforms:!1,accounts:!1}),Qe=S(()=>Z.value.groups||Z.value.platforms||Z.value.accounts);let $e=!1;const ye=S(()=>[{label:"不限制（全部可用模型）",value:0},...ke.value.map(a=>({label:a.name+(a.effective_count!=null?"（"+a.effective_count+" 个模型）":""),value:a.id}))]),bt=S(()=>[{label:"不限制（全部平台）",value:""},...Fe.value.map(a=>({label:a.label,value:a.storage}))]),mt=S(()=>Ae.value.map(a=>({label:(a.name||a.uid||"账号")+" #"+a.id+(a.status&&a.status!=="active"?" · 已禁用":"")+(a.balance_remain!=null?" · "+Te(a.balance_remain)+" 积分":""),value:a.id}))),gt=S(()=>Y.value?"编辑 API Key":"创建 API Key"),je=S(()=>Y.value?"保存":"创建"),Le=h(!1),qe=h(!1),Ee=h(""),_e=h(null),oe=h(!1),u=h(!1),l=h(""),b=h("1"),w=h(""),I=h(Wt());let Q=0;const H=h(!1),le=h(""),ue=h(!1),Je=h("openai"),yt=S(()=>[{key:"ip",title:"IP 地址",mobile:"title",width:150},{key:"requests",title:"调用次数",mobile:"meta",width:100,numeric:!0,render:a=>xe(a.requests)},{key:"failed",title:"失败",mobile:"meta",width:80,numeric:!0,render:a=>xe(a.failed)},{key:"credits",title:"积分",mobile:"meta",width:90,numeric:!0,render:a=>Te(a.credits)},{key:"tokens",title:"Token",mobile:"meta",width:100,numeric:!0,render:a=>xe(a.tokens)},{key:"lastSeen",title:"最近调用",mobile:"meta",width:150},{key:"models",title:"使用模型",mobile:"subtitle",minWidth:180},{key:"active",title:"并发",mobile:"meta",width:80,numeric:!0,render:a=>xe(a.active)},{key:"blocked",title:"状态",mobile:"meta",width:90,render:a=>v(lt,{tone:a.blocked?"danger":"ok"},{default:()=>a.blocked?"已封禁":"正常"})}]),Ye=S(()=>sr(V.value,le.value)),ht=S(()=>I.value.rows);function xt(a){if(typeof a=="string")return a;try{return JSON.stringify(a,null,2)}catch{return String(a)}}async function de(){x?.abort();const a=new AbortController;x=a,g.value=!0,k.value=null;try{const t=await Wn(a.signal);f.value=Array.isArray(t?.items)?t.items:[];const s=Math.max(1,Math.ceil(z.value.length/o.state.pageSize));o.state.page>s&&o.setPage(s)}catch(t){if(un(t))return;k.value={message:Et(t),detail:t instanceof dn&&t.detail?xt(t.detail):""},r.error(Oe(f.value===null?"加载 Key":"刷新 Key",t))}finally{x===a&&(x=null,g.value=!1)}}async function Ze(){Dn().then(a=>{E.value=a}).catch(()=>{E.value=null}),Vn().then(a=>{ae.value=a}).catch(()=>{ae.value=null}),T.value=!0;try{V.value=await Gn()}catch{V.value=null}finally{T.value=!1}}async function et(a){ie.value=!0;try{const t=await Qn({search:a,pageSize:50});return Ae.value=Array.isArray(t?.items)?t.items:t?.accounts||[],Z.value.accounts=!1,!0}catch{return Ae.value=[],Z.value.accounts=!0,!1}finally{ie.value=!1}}async function kt(){try{const a=await Xn();ke.value=Array.isArray(a?.items)?a.items:[],Z.value.groups=!1}catch{ke.value=[],Z.value.groups=!0}}async function _t(){try{const a=await Jn(),t=new Map;for(const s of a?.items||[]){const A=String(s.storage_platform||s.id||"").toLowerCase();!A||t.has(A)||t.set(A,String(s.label||A))}Fe.value=[...t].map(([s,A])=>({storage:s,label:A})),Z.value.platforms=!1}catch{Fe.value=[],Z.value.platforms=!0}}async function Ue(){if(!($e||ge.value)){$e=!0,ge.value=!0,Z.value={groups:!1,platforms:!1,accounts:!1};try{await Promise.all([kt(),_t(),et("")])}finally{ge.value=!1,Qe.value&&($e=!1)}}}async function Ct(){ge.value||($e=!1,await Ue())}const Ce=h(o.state.filters.q);Ve(()=>o.state.filters.q,a=>{Ce.value=a});const Ke=[{label:"状态：全部",value:""},{label:"正常",value:"active"},{label:"已停用",value:"revoked"}],wt=S(()=>[{label:"分组：全部",value:""},{label:"未绑定分组",value:"none"},...ke.value.map(a=>({label:a.name,value:String(a.id)}))]),St=S(()=>{const a=o.state.filters,t=[];if(a.q&&t.push({key:"q",label:"搜索",value:a.q}),a.group){const s=a.group==="none"?"未绑定分组":ke.value.find(A=>String(A.id)===a.group)?.name||a.group;t.push({key:"group",label:"分组",value:s})}return a.status&&t.push({key:"status",label:"状态",value:a.status==="active"?"正常":"已停用"}),t}),tt={name:"name",limit:"limit",used:"used",concurrency:"concurrency"};function He(a){return o.state.sortBy!==a?!1:o.state.sortOrder==="asc"?"ascend":o.state.sortOrder==="desc"?"descend":!1}function Tt(a){if(!a||!a.order||!tt[String(a.columnKey)]){o.setSort("","");return}o.setSort(tt[String(a.columnKey)],a.order==="ascend"?"asc":"desc")}function Pt(a){const t={};for(const[s,A]of Object.entries(a))t[s]=String(A??"");o.setFilters(t)}function at(a,t){o.setFilter(a,t==null?"":String(t))}function Rt(a){a==="q"&&(Ce.value=""),o.setFilter(a,"")}function nt(){Ce.value="",o.reset()}function zt(a){const t=a;return v("div",{class:"keys-name"},[v("span",{class:"keys-name__main",title:t.name},t.name),v("span",{class:"keys-name__sub"},t.accountLabel)])}function va(a){const t=a;return v("div",{class:"keys-key"},[v("code",{class:"mono keys-key__text",title:t.masked},t.masked),v(nn,{icon:Za,label:"复制完整 Key",onClick:()=>{Ut(t)}})])}function ba(a){const t=a;return v("div",{class:"keys-quota"},[v("div",{class:"keys-quota__line tnum"},[v("span",{},t.usedText),v("span",{class:"keys-quota__sep"}," / "),v("span",{},t.limitText)]),t.unlimited?v("span",{class:"keys-quota__hint"},"不限额"):v(Jt,{value:t.usageRatio*100,tone:t.usageTone,height:4,ariaLabel:"额度使用率"})])}function ma(a){const t=a;return v("div",{class:"keys-concurrency"},[v("span",{class:"tnum"},t.concurrencyText),v("span",{class:"keys-concurrency__rate"},t.rateText)])}function ga(a){const t=a;return v("div",{class:"keys-ips"},[v("button",{type:"button",class:"keys-ips__count",title:"查看该 Key 的来源 IP 与请求次数，用于识别共享范围与异常来源",onClick:()=>{Ra(t)}},t.ipText),t.ipBadges.length?v("div",{class:"keys-ips__badges"},t.ipBadges.map(s=>v(lt,{key:s.ip,tone:"brand",title:s.text},{default:()=>s.ip}))):null])}function ya(a){const t=a;return t.groupId?v(lt,{tone:"brand",title:"该 Key 仅可使用所绑定分组内的模型"},{default:()=>t.groupLabel}):v("span",{class:"keys-muted"},t.groupLabel)}function ha(a){return v("span",{class:"keys-muted",title:"后端 Key 模型没有到期字段，到期时间未提供"},a.expireText)}function xa(a){const t=a;return v(lt,{tone:t.statusTone,dot:!0},{default:()=>t.statusLabel})}function ka(a){const t=a;return v(ln,{actions:[{key:"edit",label:"编辑"},{key:"copy",label:"复制 Key"},{key:"reset",label:"重置用量"},{key:"toggle",label:t.status==="active"?"停用":"启用"},{key:"usage",label:"查看用量"},{key:"delete",label:"删除",danger:!0}],max:2,onSelect:s=>{Ta(t,s)}})}const _a=S(()=>[{key:"name",title:"名称",mobile:"title",minWidth:180,sortable:!0,sortOrder:He("name"),render:zt},{key:"key",title:"Key",mobile:"subtitle",minWidth:220,render:va},{key:"quota",title:"限额 / 已用",mobile:"meta",minWidth:170,sortable:!0,sortOrder:He("used"),render:ba},{key:"concurrency",title:"并发",mobile:"meta",width:120,sortable:!0,sortOrder:He("concurrency"),render:ma},{key:"ips",title:"来源 IP",mobile:"meta",minWidth:170,render:ga},{key:"group",title:"分组",mobile:"meta",width:130,render:ya},{key:"expire",title:"到期",mobile:"meta",width:90,render:ha},{key:"status",title:"状态",mobile:"meta",width:96,render:xa},{key:"actions",title:"操作",mobile:"actions",width:150,hideable:!1,fixed:"right",render:ka}]);function Ca(a){const t=Array.isArray(a)?a:a===null||a===""?[]:[a];R.value.accountIds=t.map(Number).filter(s=>Number.isFinite(s)&&s>0)}async function Ut(a){try{const t=await jn(a.id);await en(t.key)?r.success("已复制完整 Key"):r.error("复制失败：浏览器拒绝了剪贴板访问，请手动选中复制")}catch(t){r.error(Oe("读取 Key",t))}}function Ht(){Y.value=null,R.value=Re(),se.value={},X.value=!0,Ue()}function wa(a){Y.value=a,R.value={name:a.name,note:a.note,groupId:a.groupId,unlimited:a.unlimited,creditLimit:a.unlimited?"":String(a.creditLimit),maxConcurrent:a.maxConcurrent==null?"":String(a.maxConcurrent),ratePerMinute:a.ratePerMinute==null?"":String(a.ratePerMinute),aggregate:a.aggregate,platform:a.platform,accountIds:a.accountIds.slice(),resetUsed:!1},se.value={},X.value=!0,Ue()}async function Sa(){if(se.value=ur(R.value,!!Y.value),!dr(se.value)){ze.value=!0;try{const a=cr(R.value);if(Y.value)await Mt(Y.value.id,{...a,reset_used:R.value.resetUsed}),r.success("已保存"),X.value=!1,await de();else{const t=await Fn({...a,credit_limit:a.credit_limit??null});X.value=!1,We.value=t.key,Ie.value=!0,await de()}}catch(a){r.error(Oe(Y.value?"保存 Key":"创建 Key",a))}finally{ze.value=!1}}}async function Ta(a,t){if(t==="edit")return wa(a);if(t==="copy")return Ut(a);if(t==="usage")return Pa(a);if(t==="reset"){if(!await d({title:"重置已用额度？",message:"已用积分归零，历史日志保留。该 Key 立即恢复全部额度。",okText:"重置用量"}))return;try{await Mt(a.id,{reset_used:!0}),r.success("已重置已用额度"),await de()}catch(A){r.error(Oe("重置用量",A))}return}if(t==="toggle"){try{await Mt(a.id,rr(a.status)),r.success(a.status==="active"?"已停用":"已启用"),await de()}catch(s){r.error(Oe(a.status==="active"?"停用 Key":"启用 Key",s))}return}if(t==="delete"){if(!await d({title:"删除该 Key？",message:"删除后该 Key 立即无法调用网关，用量记录保留。如需临时停止调用，请使用停用。",danger:!0,okText:"删除"}))return;try{await Hn(a.id),r.success("已删除"),await de()}catch(A){r.error(Oe("删除 Key",A))}}}async function Pa(a){Le.value=!0,qe.value=!0,Ee.value="",_e.value=null;try{_e.value=await qn(a.id)}catch(t){Ee.value=Et(t)}finally{qe.value=!1}}async function It(){if(Q){u.value=!0,l.value="";try{const a=await Un(Q,{days:Number(b.value)||1});I.value=pa(a)}catch(a){l.value=Et(a),I.value=Wt()}finally{u.value=!1}}}async function Ra(a){Q=a.id,w.value=a.name,b.value="1",I.value=Wt(),l.value="",oe.value=!0,await It()}const za=S(()=>[{label:"来源 IP",value:xe(I.value.totalIps)},{label:"调用次数",value:xe(I.value.requests)},{label:"当前活跃 IP",value:xe(I.value.activeIps)},{label:"当前并发",value:xe(I.value.activeRequests)}]),me=S(()=>_e.value?fr(_e.value):null);return ua(()=>{de(),Ze()}),Ba(()=>{x?.abort()}),(a,t)=>(L(),ee("div",pr,[c(Ja,{title:"API Keys",help:Jr},{actions:m(()=>[c(i(J),{variant:"quiet",icon:i(mn),loading:g.value,onClick:t[0]||(t[0]=s=>de())},{default:m(()=>[...t[35]||(t[35]=[N("刷新",-1)])]),_:1},8,["icon","loading"]),y("span",vr,[c(i(J),{variant:"quiet",onClick:t[1]||(t[1]=s=>i(n).push("/credits"))},{default:m(()=>[N(D(Xe.value),1)]),_:1}),c(i(Vt),{content:Pe.value,label:"账号调度策略说明"},null,8,["content"])]),c(i(J),{variant:"default",onClick:t[2]||(t[2]=s=>ue.value=!0)},{default:m(()=>[...t[36]||(t[36]=[N("接入指南",-1)])]),_:1}),c(i(J),{variant:"primary",icon:i(Yt),onClick:Ht},{default:m(()=>[...t[37]||(t[37]=[N("创建 Key",-1)])]),_:1},8,["icon"])]),_:1}),c(i(Gt),{class:"keys-share",title:"对接信息"},{extra:m(()=>[c(i(J),{variant:"quiet",size:"sm",loading:T.value,onClick:t[3]||(t[3]=s=>Ze())},{default:m(()=>[...t[38]||(t[38]=[N("刷新",-1)])]),_:1},8,["loading"])]),default:m(()=>[y("div",br,[y("div",mr,[t[39]||(t[39]=y("span",{class:"keys-share__label"},"连接地址",-1)),c(i(Be),{class:"keys-share__select",value:G.value,options:F.value,"onUpdate:value":t[4]||(t[4]=s=>G.value=s)},null,8,["value","options"])]),y("div",gr,[t[40]||(t[40]=y("span",{class:"keys-share__label"},[N("OpenAI"),y("em",null,"含 /v1")],-1)),c(i(ut),{value:ve.value},null,8,["value"])]),y("div",yr,[t[41]||(t[41]=y("span",{class:"keys-share__label"},[N("Claude"),y("em",null,"不含 /v1")],-1)),c(i(ut),{value:vt.value},null,8,["value"])]),y("div",hr,[t[42]||(t[42]=y("span",{class:"keys-share__label"},"鉴权",-1)),c(i(ut),{value:"Authorization: Bearer <你的 Key>　或　x-api-key: <你的 Key>"}),c(i(Vt),{content:Yr,label:"鉴权说明"})]),y("div",xr,[t[43]||(t[43]=y("span",{class:"keys-share__label"},"可用模型",-1)),y("div",kr,[y("span",_r,D(be.value.total)+" 组",1),(L(!0),ee(Ne,null,rt(be.value.chips,s=>(L(),re(i(Xt),{key:s.key,mono:"",copy:"",value:s.copyValue,title:s.title},{default:m(()=>[N(D(s.name),1),s.platformCount>1?(L(),ee("span",Cr,D(s.platformCount),1)):he("",!0)]),_:2},1032,["value","title"]))),128)),be.value.total?(L(),re(i(J),{key:0,variant:"quiet",size:"sm",onClick:t[5]||(t[5]=s=>H.value=!0)},{default:m(()=>[N(" 全部 "+D(be.value.total)+" ▸ ",1)]),_:1})):he("",!0),!be.value.total&&!T.value?(L(),ee("span",wr,"未配置模型白名单（放行全部模型）")):he("",!0)])])])]),_:1}),c(i(Gt),{flush:"",class:"keys-card"},{default:m(()=>[y("div",Sr,[c(i(sn),{chips:St.value,summary:q.value,loading:g.value,onChange:Pt,onReset:nt,onRemove:Rt},{default:m(({change:s})=>[c(i(we),{class:"keys-filter__search",value:Ce.value,placeholder:"搜索名称 / 备注 / 账号",clearable:"","onUpdate:value":A=>{Ce.value=A,s("q",A)}},null,8,["value","onUpdate:value"]),c(i(Be),{class:"keys-filter__select",value:i(o).state.filters.group,options:wt.value,"onUpdate:value":t[6]||(t[6]=A=>at("group",A))},null,8,["value","options"]),c(i(Be),{class:"keys-filter__select",value:i(o).state.filters.status,options:Ke,"onUpdate:value":t[7]||(t[7]=A=>at("status",A))},null,8,["value"])]),_:1},8,["chips","summary","loading"])]),y("div",Tr,[k.value&&f.value===null?(L(),re(i(st),{key:0,title:"加载 Key 失败："+k.value.message,detail:k.value.detail,onRetry:t[8]||(t[8]=s=>de())},null,8,["title","detail"])):B.value?(L(),ee("div",Pr,[(L(),ee(Ne,null,rt(6,s=>c(i(ea),{key:s,height:"44px"})),64))])):(L(),re(i(Qt),{key:2,"table-id":"keys",columns:_a.value,data:j.value.items,"row-key":"id",remote:"",loading:g.value,page:i(o).state.page,"page-size":i(o).state.pageSize,"item-count":j.value.total,"page-sizes":[20,50,100],"empty-description":W.value,"onUpdate:page":i(o).setPage,"onUpdate:pageSize":i(o).setPageSize,"onUpdate:sorter":Tt},{empty:m(()=>[c(i(an),{description:W.value},{default:m(()=>[K.value?(L(),re(i(J),{key:0,variant:"default",size:"sm",onClick:nt},{default:m(()=>[...t[44]||(t[44]=[N("清除筛选",-1)])]),_:1})):(L(),re(i(J),{key:1,variant:"primary",size:"sm",icon:i(Yt),onClick:Ht},{default:m(()=>[...t[45]||(t[45]=[N("创建 Key",-1)])]),_:1},8,["icon"]))]),_:1},8,["description"])]),_:1},8,["columns","data","loading","page","page-size","item-count","empty-description","onUpdate:page","onUpdate:pageSize"]))])]),_:1}),c(i(fn),{show:X.value,"onUpdate:show":t[20]||(t[20]=s=>X.value=s),title:gt.value,size:"lg","submit-text":je.value,"submit-loading":ze.value,onSubmit:Sa},{default:m(()=>[Qe.value?(L(),re(i(st),{key:0,compact:"",title:"分组 / 平台 / 账号候选加载失败，对应下拉可能为空。","retry-label":"重新加载",onRetry:Ct})):he("",!0),c(i(it),{title:"基本",first:""},{default:m(()=>[y("div",Rr,[c(i(pe),{label:"名称",required:!!Y.value,error:se.value.name,hint:"创建时可留空，后端会填「未命名」。"},{default:m(()=>[c(i(we),{value:R.value.name,"onUpdate:value":t[9]||(t[9]=s=>R.value.name=s),placeholder:"例如：给设计组的 Key",maxlength:"120",clearable:""},null,8,["value"])]),_:1},8,["required","error"]),c(i(pe),{label:"模型分组",hint:"选择后该 Key 只能调用组内模型，调用组外模型返回 403。"},{default:m(()=>[c(i(Be),{value:R.value.groupId,"onUpdate:value":t[10]||(t[10]=s=>R.value.groupId=s),options:ye.value},null,8,["value","options"])]),_:1}),c(i(pe),{label:"备注"},{default:m(()=>[c(i(we),{value:R.value.note,"onUpdate:value":t[11]||(t[11]=s=>R.value.note=s),type:"textarea",rows:2,maxlength:"255",placeholder:"可选"},null,8,["value"])]),_:1})])]),_:1}),c(i(it),{title:"额度与流量"},{default:m(()=>[y("div",zr,[c(i(pe),{label:"总额度（积分）",error:se.value.creditLimit,hint:"勾选「不限额」时忽略；填 0 表示零额度。"},{default:m(()=>[y("div",Ir,[c(i(Lt),{checked:R.value.unlimited,"onUpdate:checked":t[12]||(t[12]=s=>R.value.unlimited=s)},{default:m(()=>[...t[46]||(t[46]=[N("不限额（∞）",-1)])]),_:1},8,["checked"]),c(i(we),{value:R.value.creditLimit,"onUpdate:value":t[13]||(t[13]=s=>R.value.creditLimit=s),placeholder:"留空不限额；0 表示零额度",disabled:R.value.unlimited},null,8,["value","disabled"])])]),_:1},8,["error"]),c(i(pe),{label:"并发上限",error:se.value.maxConcurrent,hint:"空 = 跟随全局；0 = 不限；N = 该 Key 所有对话合计最多 N 个在途请求。"},{default:m(()=>[c(i(we),{value:R.value.maxConcurrent,"onUpdate:value":t[14]||(t[14]=s=>R.value.maxConcurrent=s),placeholder:"空 = 全局，0 = 不限"},null,8,["value"])]),_:1},8,["error"]),c(i(pe),{label:"每分钟请求数",error:se.value.ratePerMinute,hint:"频率与并发独立；积分不限额也不会解除流量限制。"},{default:m(()=>[c(i(we),{value:R.value.ratePerMinute,"onUpdate:value":t[15]||(t[15]=s=>R.value.ratePerMinute=s),placeholder:"空 = 全局，0 = 不限"},null,8,["value"])]),_:1},8,["error"]),Y.value?(L(),re(i(pe),{key:0,label:"重置已用额度"},{default:m(()=>[c(i(Lt),{checked:R.value.resetUsed,"onUpdate:checked":t[16]||(t[16]=s=>R.value.resetUsed=s)},{default:m(()=>[N(" 勾选后把已用积分归零（当前 "+D(Y.value?.usedText)+"，不影响历史日志） ",1)]),_:1},8,["checked"])]),_:1})):he("",!0)])]),_:1}),c(i(it),{title:"模型与账号"},{default:m(()=>[y("div",Ar,[c(i(pe),{label:"聚合模型路由",hint:"开启后 /v1/models 返回跨平台聚合列表，可由网关挑平台转发。"},{default:m(()=>[c(i(Lt),{checked:R.value.aggregate,"onUpdate:checked":t[17]||(t[17]=s=>R.value.aggregate=s)},{default:m(()=>[...t[47]||(t[47]=[N("使用聚合模型（跨平台负载分摊）",-1)])]),_:1},8,["checked"])]),_:1}),c(i(pe),{label:"独占平台",hint:"绑定后只在该平台的账号池里选号；不限制则按请求自报平台。"},{default:m(()=>[c(i(Be),{value:R.value.platform,"onUpdate:value":t[18]||(t[18]=s=>R.value.platform=s),options:bt.value},null,8,["value","options"])]),_:1}),c(i(pe),{label:"独占绑定账号（多选）",hint:"不选 = 使用公共账号池；选中后只用这些账号，账号不可用时不换号。搜索走服务端。"},{default:m(()=>[c(i(Be),{value:R.value.accountIds,multiple:"",filterable:"",remote:"",clearable:"",loading:ie.value,options:mt.value,placeholder:"不限制（使用公共账号池）","onUpdate:value":Ca,onSearch:t[19]||(t[19]=s=>et(s))},null,8,["value","loading","options"])]),_:1})])]),_:1}),c(i(it),{title:"访问与有效期"},{default:m(()=>[...t[48]||(t[48]=[y("p",{class:"keys-hint"}," 来源 IP 只用于统计（表格「来源 IP」列可查看），后端没有「绑定来源 IP」字段，因此这里不提供绑定位。 ",-1),y("p",{class:"keys-hint"},"后端 Key 模型没有到期字段，因此不提供有效期设置，表格「到期」列显示 —。",-1)])]),_:1})]),_:1},8,["show","title","submit-text","submit-loading"]),c(i(Ya),{show:Ie.value,"onUpdate:show":t[22]||(t[22]=s=>Ie.value=s),preset:"card",class:"keys-modal",title:"Key 已创建",style:{width:"520px"}},{footer:m(()=>[y("div",$r,[c(i(J),{variant:"primary",onClick:t[21]||(t[21]=s=>Ie.value=!1)},{default:m(()=>[...t[49]||(t[49]=[N("完成",-1)])]),_:1})])]),default:m(()=>[t[50]||(t[50]=y("p",{class:"keys-hint"},"可随时在列表里点「复制 Key」再次查看完整值。",-1)),c(i(ut),{value:We.value},null,8,["value"])]),_:1},8,["show"]),c(i(dt),{show:Le.value,"onUpdate:show":t[24]||(t[24]=s=>Le.value=s),title:"Key 用量",subtitle:"按额度统计"},{footer:m(()=>[c(i(J),{variant:"default",onClick:t[23]||(t[23]=s=>Le.value=!1)},{default:m(()=>[...t[55]||(t[55]=[N("关闭",-1)])]),_:1})]),default:m(()=>[Ee.value?(L(),re(i(st),{key:0,title:"读取用量失败："+Ee.value,retry:!1,compact:""},null,8,["title"])):qe.value?(L(),ee("div",Lr,[c(i(ea),{repeat:3,text:""})])):me.value?(L(),ee(Ne,{key:2},[y("div",Er,[y("div",Kr,[t[51]||(t[51]=y("span",null,"限额",-1)),y("strong",Or,D(me.value.limitText),1)]),y("div",Br,[t[52]||(t[52]=y("span",null,"已用",-1)),y("strong",Nr,D(me.value.usedText),1)]),y("div",Mr,[t[53]||(t[53]=y("span",null,"剩余",-1)),y("strong",Wr,D(me.value.remainingText),1)]),y("div",Fr,[t[54]||(t[54]=y("span",null,"使用率",-1)),y("strong",jr,D(me.value.percentText),1)])]),me.value.unlimited?he("",!0):(L(),re(i(Jt),{key:0,value:me.value.ratio*100,tone:me.value.tone,height:8,"aria-label":"额度使用率"},null,8,["value","tone"])),y("p",qr,"创建于 "+D(me.value.createdText),1)],64)):he("",!0)]),_:1},8,["show"]),c(i(dt),{show:oe.value,"onUpdate:show":t[28]||(t[28]=s=>oe.value=s),title:"来源 IP · "+w.value,subtitle:"并发为当前服务进程的在途请求"},{footer:m(()=>[c(i(J),{variant:"default",onClick:t[27]||(t[27]=s=>oe.value=!1)},{default:m(()=>[...t[57]||(t[57]=[N("关闭",-1)])]),_:1})]),default:m(()=>[y("div",Ur,[c(i(rn),{options:[{label:"24 小时",value:"1"},{label:"7 天",value:"7"},{label:"30 天",value:"30"}],"model-value":b.value,"aria-label":"时间范围","onUpdate:modelValue":t[25]||(t[25]=s=>{b.value=String(s),It()})},null,8,["model-value"]),c(i(J),{variant:"quiet",size:"sm",loading:u.value,onClick:t[26]||(t[26]=s=>It())},{default:m(()=>[...t[56]||(t[56]=[N("刷新",-1)])]),_:1},8,["loading"])]),c(i(on),{items:za.value},null,8,["items"]),l.value?(L(),re(i(st),{key:0,title:"读取来源 IP 失败："+l.value,retry:!1,compact:""},null,8,["title"])):(L(),re(i(Qt),{key:1,"table-id":"keys-ips",columns:yt.value,data:ht.value,"row-key":"ip",pagination:!1,"virtual-scroll":!1,"max-height":"360px","empty-description":"所选时间内暂无调用记录"},null,8,["columns","data"])),t[58]||(t[58]=y("p",{class:"keys-hint"},"封禁 / 解封在「IP 管理」页操作；这里只读展示统计。",-1))]),_:1},8,["show","title"]),c(i(dt),{show:H.value,"onUpdate:show":t[31]||(t[31]=s=>H.value=s),title:"可用模型 · "+be.value.total+" 组",subtitle:"点击模型名复制调用名（带平台后缀）"},{footer:m(()=>[c(i(J),{variant:"default",onClick:t[30]||(t[30]=s=>H.value=!1)},{default:m(()=>[...t[59]||(t[59]=[N("关闭",-1)])]),_:1})]),default:m(()=>[c(i(we),{value:le.value,"onUpdate:value":t[29]||(t[29]=s=>le.value=s),placeholder:"搜索模型名 / 调用名 / 上游 id",clearable:""},null,8,["value"]),Ye.value.length?he("",!0):(L(),ee("div",Hr,"没有匹配的模型。")),(L(!0),ee(Ne,null,rt(Ye.value,s=>(L(),ee("div",{key:s.key,class:"keys-model"},[y("div",Dr,[y("strong",null,D(s.name),1),y("span",Vr,D(s.platformCount)+" 平台",1)]),y("div",Gr,[(L(!0),ee(Ne,null,rt(s.rows,A=>(L(),ee("div",{key:A.callId,class:"keys-model__row"},[y("span",Xr,D(A.platformLabel),1),c(i(Xt),{mono:"",copy:"",value:A.callId,title:"上游 id："+A.upstreamId},{default:m(()=>[N(D(A.callId),1)]),_:2},1032,["value","title"]),y("span",Qr,D(A.multiplierText),1)]))),128))])]))),128))]),_:1},8,["show","title"]),c(i(dt),{show:ue.value,"onUpdate:show":t[34]||(t[34]=s=>ue.value=s),title:"接入指南",subtitle:"地址与模型已按当前连接地址填好"},{footer:m(()=>[c(i(J),{variant:"default",onClick:t[33]||(t[33]=s=>ue.value=!1)},{default:m(()=>[...t[63]||(t[63]=[N("关闭",-1)])]),_:1})]),default:m(()=>[c(i(Mn),{value:Je.value,"onUpdate:value":t[32]||(t[32]=s=>Je.value=s),type:"line",animated:""},{default:m(()=>[c(i(De),{name:"openai",tab:"OpenAI 协议"},{default:m(()=>[c(i(Se),{code:ne.value.openai,language:"bash"},null,8,["code"])]),_:1}),c(i(De),{name:"claude",tab:"Claude Code / Anthropic"},{default:m(()=>[c(i(Se),{code:ne.value.claude,language:"bash"},null,8,["code"])]),_:1}),c(i(De),{name:"audio",tab:"语音转文字"},{default:m(()=>[t[60]||(t[60]=y("p",{class:"keys-hint"}," POST /v1/audio/transcriptions，使用同一 API Key：multipart/form-data 上传 file， model=workbuddy-asr；response_format 可选 json（默认）或 text。网关文件上限 25 MiB， 不支持 language / prompt / 时间戳 / 流式转写；不保存音频与转写内容。 ",-1)),c(i(Se),{code:ne.value.audioCurl,language:"bash"},null,8,["code"]),c(i(Se),{code:ne.value.audioPython,language:"python"},null,8,["code"])]),_:1}),c(i(De),{name:"images",tab:"AI 生图"},{default:m(()=>[t[61]||(t[61]=y("p",{class:"keys-hint"}," 文生图 POST /v1/images/generations，图生图 POST /v1/images/edits（单张 image，最大 20 MiB； 也支持 image_url / base64 image_data）。返回 url（默认）或 b64_json；暂不支持 mask、流式、多图。 ",-1)),c(i(Se),{code:ne.value.imagesPython,language:"python"},null,8,["code"])]),_:1}),c(i(De),{name:"videos",tab:"AI 生视频（异步任务）"},{default:m(()=>[t[62]||(t[62]=y("p",{class:"keys-hint"}," 创建 POST /v1/videos 只到「上游已入队」就返回 queued；轮询 GET /v1/videos/{id}， 下载 GET /v1/videos/{id}/content。生成通常需要 1~5 分钟，按上游 usage.credit 实际积分记账。 ",-1)),c(i(Se),{code:ne.value.videoCurl,language:"bash"},null,8,["code"]),c(i(Se),{code:ne.value.videoPython,language:"python"},null,8,["code"])]),_:1})]),_:1},8,["value"])]),_:1},8,["show"])]))}}),jo=cn(Zr,[["__scopeId","data-v-82d74e62"]]);export{jo as default};
